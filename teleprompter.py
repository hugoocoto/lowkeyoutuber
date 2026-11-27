#!/usr/bin/env python3
"""
teleprompter.py - Read the script while you record. A full-screen page in your browser shows
the current line big and the next one small; with OBS it also starts and stops a recording per
take (obs-websocket, built into OBS 28+), so every take is a file labelled with its line.

KEYS (in the page)
    Space        start a take / stop it                        (OBS: StartRecord / StopRecord)
    Right, Enter next line          Left  previous line
    B            the last take of this line was bad (it won't be used)
    R            retake: stop the current take and start a new one at once
    Q            finish (stops a running take)

MODES
    --obs        one recording per take, controlled from the page. In OBS: Tools > WebSocket
                 Server Settings > Enable. Password: environment OBS_WEBSOCKET_PASSWORD;
                 address: config OBS_WEBSOCKET (default localhost:4455).
    (default)    you record one long take yourself; the page only shows the text and logs when
                 you moved to each line (marks), which align.py doesn't need but you can check.
                 They go to proposals/marks.json; with --obs the takes go to proposals/takes-recorded.json
                 and reopening keeps the takes already there.

THEN
    align.py -P project/ --from-takes      # picks the last good take of each line
    align.py -P project/ --from-takes --apply

USAGE
    teleprompter.py -P project/ --obs
    teleprompter.py -P project/ --port 8766 --no-open
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import project as proj
from yt_common import CONFIG, die, log

PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Teleprompter</title>
<style>
html,body{margin:0;height:100%;background:#0b0c10;color:#f2f2f2;font-family:"DejaVu Sans",sans-serif}
#wrap{display:flex;flex-direction:column;justify-content:center;height:100%;padding:0 6vw;box-sizing:border-box}
#cur{font-size:6.2vw;line-height:1.25;font-weight:600}
#next{font-size:3vw;line-height:1.3;color:#6b7280;margin-top:5vh}
#bar{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-between;padding:1.4vh 2vw;
     font-size:2vw;color:#9ca3af}
#rec{color:#9ca3af}#rec.on{color:#ff4d4d;font-weight:700}
#help{position:fixed;bottom:1.4vh;left:2vw;font-size:1.4vw;color:#4b5563}
#err{position:fixed;bottom:5vh;left:2vw;right:2vw;font-size:1.6vw;color:#ff8080}
</style></head><body>
<div id="bar"><span id="pos"></span><span id="rec"></span><span id="takes"></span></div>
<div id="wrap"><div id="cur"></div><div id="next"></div></div>
<div id="err"></div>
<div id="help">Space: start/stop take · →/Enter: next · ←: back · B: bad take · R: retake · Q: finish</div>
<script>
let queue=Promise.resolve();   // one request at a time, so answers are shown in the order they were asked
function call(a){queue=queue.then(()=>send(a)).catch(e=>lost(e));}
function lost(e){   // the answer never came: the recording state is unknown until a refresh works
  const r=document.getElementById('rec');r.textContent='? REC unknown';r.className='';
  document.getElementById('err').textContent='No answer from teleprompter.py ('+e+'). Check OBS before going on; '
    +'any key retries.';
  queue=Promise.resolve();
  setTimeout(()=>call('state'),1500);
}
async function send(a){const r=await fetch('/api/'+a,{method:a==='state'?'GET':'POST'});const s=await r.json();show(s);}
function show(s){
  document.getElementById('cur').textContent=s.done?'Done. You can close this page.':s.current||'';
  document.getElementById('next').textContent=s.next||'';
  document.getElementById('pos').textContent=s.done?'':`${s.index+1} / ${s.total}  ·  ${s.id}`;
  const r=document.getElementById('rec');r.textContent=s.recording?'● REC':(s.obs?'○ ready':'no OBS');
  r.className=s.recording?'on':'';
  document.getElementById('takes').textContent=s.takes_here?`takes: ${s.takes_here}`:'';
  document.getElementById('err').textContent=s.error||'';
}
document.addEventListener('keydown',e=>{
  const k=e.key; let a=null;
  if(k===' ')a='toggle'; else if(k==='ArrowRight'||k==='Enter')a='next'; else if(k==='ArrowLeft')a='prev';
  else if(k==='b'||k==='B')a='bad'; else if(k==='r'||k==='R')a='retake'; else if(k==='q'||k==='Q')a='finish';
  if(a){e.preventDefault();call(a);}
});
call('state');
</script></body></html>"""


IDLE_WAIT = 5.0   # seconds to wait for OBS to finish a recording before starting the next


class OBSError(RuntimeError):
    pass


class OBS:
    """Minimal obs-websocket v5 client: Hello -> Identify (with auth) -> requests."""

    def __init__(self, url, password=None, timeout=10):
        try:
            import websocket
        except ImportError:
            raise OBSError("websocket-client is not installed: pip install websocket-client")
        try:
            self.ws = websocket.create_connection(url, timeout=timeout)
        except (OSError, websocket.WebSocketException) as e:
            raise OBSError(f"can't connect to OBS at {url} ({e}). In OBS: Tools > WebSocket Server Settings")
        hello = json.loads(self.ws.recv())
        if hello.get("op") != 0:
            raise OBSError(f"unexpected first message from OBS: {hello}")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello["d"].get("authentication")
        if auth:
            if not password:
                raise OBSError("OBS asks for a password: set OBS_WEBSOCKET_PASSWORD in the environment")
            secret = base64.b64encode(hashlib.sha256((password + auth["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(
                hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        self.ws.send(json.dumps({"op": 1, "d": ident}))
        try:
            msg = json.loads(self.ws.recv())
        except Exception:
            raise OBSError("OBS closed the connection: wrong password?")
        if msg.get("op") != 2:
            raise OBSError(f"OBS didn't accept the connection: {msg}")
        self.lock = threading.Lock()

    def request(self, kind, data=None):
        rid = uuid.uuid4().hex
        with self.lock:
            try:
                self.ws.send(json.dumps({"op": 6, "d": {"requestType": kind, "requestId": rid, "requestData": data or {}}}))
                while True:
                    msg = json.loads(self.ws.recv())
                    if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                        break
            except Exception as e:   # socket closed, timeout, garbage: we don't know if OBS did it
                raise OBSError(f"lost the connection to OBS during {kind} ({e}); "
                               "the result is unknown: check OBS before going on")
        st = msg["d"].get("requestStatus", {})
        if not st.get("result"):
            raise OBSError(f"{kind} failed: {st.get('comment') or st.get('code')}")
        return msg["d"].get("responseData") or {}

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


class Prompter:
    def __init__(self, lines, out_path, obs=None):
        self.lines, self.out_path, self.obs = lines, out_path, obs
        self.i, self.recording, self.take_start, self.done, self.error = 0, False, None, False, ""
        self.t0 = time.monotonic()
        self.data = {"mode": "obs" if obs else "marks", "takes": [], "marks": []}
        if obs:   # reopened to record the missing lines: keep the takes already recorded
            try:
                with open(out_path, encoding="utf-8") as f:
                    old = json.load(f)
                if old.get("mode") == "obs":
                    self.data["takes"], self.data["marks"] = old.get("takes", []), old.get("marks", [])
            except (OSError, ValueError, AttributeError):
                pass
        self.data["marks"].append({"line": lines[0]["id"], "t": 0.0})
        self.lock = threading.Lock()   # nothing is written until the first action: a failed start changes nothing

    def save(self):
        tmp = self.out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.out_path)

    def state(self):
        line = self.lines[self.i]
        nxt = self.lines[self.i + 1]["text"] if self.i + 1 < len(self.lines) else "(last line)"
        return {"index": self.i, "total": len(self.lines), "id": line["id"], "current": line["text"], "next": nxt,
                "recording": self.recording, "obs": bool(self.obs), "done": self.done, "error": self.error,
                "takes_here": sum(1 for t in self.data["takes"] if t["line"] == line["id"])}

    def _start(self):
        if self.obs:
            self.obs.request("StartRecord")
        self.recording, self.take_start = True, time.monotonic()

    def _stop(self):
        out = {}
        if self.obs:
            try:
                out = self.obs.request("StopRecord")
            except OBSError as e:
                try:
                    active = self.obs.request("GetRecordStatus").get("outputActive")
                except OBSError:
                    active = None
                if active is False:   # OBS did stop, but we never got the file's path
                    self.data["takes"].append({"line": self.lines[self.i]["id"], "file": None,
                                               "seconds": round(time.monotonic() - self.take_start, 2),
                                               "bad": False, "lost": True})
                    self.recording = False
                    raise OBSError(f"{e}. OBS stopped this take but its file is unknown: "
                                   "find it in OBS's recordings folder")
                raise
        self.data["takes"].append({"line": self.lines[self.i]["id"], "file": out.get("outputPath"),
                                   "seconds": round(time.monotonic() - self.take_start, 2), "bad": False})
        self.recording = False

    def _wait_idle(self):
        """StopRecord answers before the output has really stopped; StartRecord would be refused."""
        end = time.monotonic() + IDLE_WAIT
        while self.obs.request("GetRecordStatus").get("outputActive"):
            if time.monotonic() > end:
                raise OBSError("OBS is still finishing the previous take: press R again in a moment")
            time.sleep(0.1)

    def act(self, action):
        with self.lock:
            self.error = ""
            try:
                if self.done:
                    return self.state()
                if action == "toggle":
                    if not self.obs:
                        self.error = "No OBS: record on your own; use →/← to move through the lines."
                    elif self.recording:
                        self._stop()
                    else:
                        self._start()
                elif action == "retake":
                    if self.obs:
                        if self.recording:
                            self._stop()
                            self.data["takes"][-1]["bad"] = True
                            self._wait_idle()
                        self._start()
                elif action == "bad":
                    mine = [t for t in self.data["takes"] if t["line"] == self.lines[self.i]["id"]]
                    if mine:
                        mine[-1]["bad"] = True
                elif action in ("next", "prev"):
                    if self.recording:
                        self._stop()
                    self.i = min(len(self.lines) - 1, self.i + 1) if action == "next" else max(0, self.i - 1)
                    self.data["marks"].append({"line": self.lines[self.i]["id"],
                                               "t": round(time.monotonic() - self.t0, 2)})
                elif action == "finish":
                    if self.recording:
                        self._stop()
                    self.done = True
                elif action != "state":
                    self.error = f"unknown action {action}"
            except OBSError as e:
                self.error = str(e)
            self.save()
            return self.state()


def make_server(prompter, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._send(200, json.dumps(prompter.act("state")), "application/json")
            else:
                self._send(404, "{}", "application/json")

        def do_POST(self):
            # a form on another site can POST here without a preflight: only our own page may act
            origin = self.headers.get("Origin")
            port = self.server.server_address[1]
            if origin is not None and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                self._send(403, "{}", "application/json")
            elif self.path.startswith("/api/"):
                self._send(200, json.dumps(prompter.act(self.path[5:])), "application/json")
            else:
                self._send(404, "{}", "application/json")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    p = argparse.ArgumentParser(description="Teleprompter for script.json, optionally controlling OBS.")
    p.add_argument("-P", "--project", required=True)
    p.add_argument("--obs", action="store_true", help="start/stop an OBS recording per take")
    p.add_argument("--obs-url", default=None, help="default: config OBS_WEBSOCKET or localhost:4455")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = p.parse_args()
    try:
        script = proj.load_script(args.project)
    except proj.ProjectError as e:
        die(str(e))
    lines = script["lines"]
    if not lines:
        die("script.json has no lines")
    obs = None
    if args.obs:
        addr = args.obs_url or CONFIG.get("OBS_WEBSOCKET") or "localhost:4455"
        url = addr if addr.startswith("ws") else "ws://" + addr
        try:
            obs = OBS(url, os.environ.get("OBS_WEBSOCKET_PASSWORD"))
            if obs.request("GetRecordStatus").get("outputActive"):
                obs.close()
                die("OBS is already recording: stop that recording (it isn't ours), then run this again")
        except OBSError as e:
            die(str(e))
        log(f"Connected to OBS at {url}.")
    # the OBS takes are what align.py --from-takes reads: a marks-only run must never overwrite them
    out = os.path.join(proj.root(args.project), "proposals", "takes-recorded.json" if obs else "marks.json")
    prompter = Prompter(lines, out, obs)
    srv = make_server(prompter, args.port)
    url = f"http://127.0.0.1:{args.port}/"
    log(f"Teleprompter: {url}  (full screen with F11; Q in the page to finish)")
    if not args.no_open:
        webbrowser.open(url)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        while not prompter.done:
            time.sleep(0.3)
    except KeyboardInterrupt:
        prompter.act("finish")
    srv.shutdown()
    if obs:
        obs.close()
    takes = prompter.data["takes"]
    if not obs:
        log(f"Marks: {out}")
    log(f"Finished: {len(takes)} takes ({sum(t['bad'] for t in takes)} marked bad) -> {out}")
    try:
        pj = proj.load(args.project)
        proj.set_state(pj, "recording", "done")
        proj.save(args.project, pj)
    except proj.ProjectError:
        pass
    if takes:
        log(f"Next: align.py -P {args.project} --from-takes")
    sys.exit(0)


if __name__ == "__main__":
    main()
