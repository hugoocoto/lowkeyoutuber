"""The OBS password never crosses the network unencrypted (no websocket package needed)."""

import pytest

import teleprompter
from teleprompter import OBS, OBSError, is_loopback


def test_loopback_hosts():
    for url in ("ws://localhost:4455", "ws://127.0.0.1:4455", "ws://[::1]:4455"):
        assert is_loopback(url), url
    for url in ("ws://192.168.1.20:4455", "ws://obs.lan:4455"):
        assert not is_loopback(url), url


def test_password_to_a_remote_host_needs_wss(monkeypatch):
    with pytest.raises(OBSError, match="wss://"):
        OBS("ws://192.168.1.20:4455", password="secret")


def test_local_and_wss_and_passwordless_are_allowed(monkeypatch):
    # the transport check passes: what fails next is the websocket package or the connection
    # (port 1 and a TEST-NET address: never a real OBS)
    for url, pw in (("ws://localhost:1", "secret"), ("wss://192.0.2.1:1", "secret"), ("ws://192.0.2.1:1", None)):
        with pytest.raises(OBSError) as e:
            OBS(url, password=pw, timeout=0.2)
        assert "unencrypted" not in str(e.value)
    assert teleprompter.is_loopback("ws://localhost")
