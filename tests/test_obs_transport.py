"""OBS safety in teleprompter.py: the password never crosses the network unencrypted, earlier takes
are never silently replaced, and a recording we didn't start is never taken over. No websocket
package needed: a fake module stands in for websocket-client."""

import sys
import types

import pytest

import teleprompter as tp
from teleprompter import OBS, OBSError, is_loopback


@pytest.fixture
def fake_ws(monkeypatch):
    """websocket-client stand-in: records create_connection's arguments, then refuses."""
    seen = {}

    class WebSocketException(Exception):
        pass

    def create_connection(url, **kw):
        seen.update(kw, url=url)
        raise OSError("connection refused (fake)")
    monkeypatch.setitem(sys.modules, "websocket", types.SimpleNamespace(
        create_connection=create_connection, WebSocketException=WebSocketException))
    for k in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(k, raising=False)
    return seen


def test_loopback_hosts():
    for url in ("ws://localhost:4455", "ws://127.0.0.1:4455", "ws://[::1]:4455"):
        assert is_loopback(url), url
    for url in ("ws://192.168.1.20:4455", "ws://obs.lan:4455"):
        assert not is_loopback(url), url


def test_password_to_a_remote_host_needs_wss(fake_ws):
    for url in ("ws://192.168.1.20:4455", "WS://192.168.1.20:4455"):   # the scheme is parsed, any case
        with pytest.raises(OBSError, match="wss://"):
            OBS(url, password="secret")
    assert fake_ws == {}   # refused before connecting


def test_not_a_websocket_address_is_refused(fake_ws):
    with pytest.raises(OBSError, match="not a websocket address"):
        OBS("http://localhost:4455", password="secret")


def test_http_proxy_blocks_a_plain_password_even_to_localhost(fake_ws, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://proxy.example:3128")
    with pytest.raises(OBSError, match="proxy"):
        OBS("ws://localhost:4455", password="secret")
    assert fake_ws == {}


def test_allowed_connections_never_follow_redirects(fake_ws):
    for url, pw in (("ws://localhost:4455", "secret"), ("wss://192.0.2.1:4455", "secret"),
                    ("ws://192.0.2.1:4455", None)):
        with pytest.raises(OBSError, match="can't connect"):
            OBS(url, password=pw)
        assert fake_ws["url"] == url and fake_ws["redirect_limit"] == 0


def test_unreadable_takes_file_is_an_error_and_is_left_alone(tmp_path):
    f = tmp_path / "takes-recorded.json"
    f.write_text("{not json")
    with pytest.raises(OBSError, match="fix or move"):
        tp.Prompter([{"id": "a", "text": "Hola"}], str(f), obs=object())
    assert f.read_text() == "{not json"


class BusyOBS:
    """OBS that is already recording something we didn't start."""
    def __init__(self):
        self.calls = []

    def request(self, kind, data=None):
        self.calls.append(kind)
        return {"outputActive": True} if kind == "GetRecordStatus" else {}


def test_a_take_does_not_start_while_obs_records_something_else(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "IDLE_WAIT", 0.2)
    obs = BusyOBS()
    pr = tp.Prompter([{"id": "a", "text": "Hola"}], str(tmp_path / "t.json"), obs=obs)
    st = pr.act("toggle")
    assert "still recording" in st["error"] and not st["recording"]
    assert "StartRecord" not in obs.calls
