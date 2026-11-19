"""Minimal pure-Python client for Firefox's built-in Marionette protocol."""

import json
import socket
from typing import Any, Dict, List, Optional


class MarionetteClient:
    """Client for driving Firefox via its Marionette remote control server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2828, timeout: float = 60.0):
        self.host = host
        self.port = port
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.msg_id = 0
        self.buf = b""
        self.handshake = self._recv_packet()

    def _recv_packet(self) -> Any:
        while b":" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("Marionette socket closed while reading length prefix")
            self.buf += chunk
        len_str, rest = self.buf.split(b":", 1)
        length = int(len_str)
        while len(rest) < length:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("Marionette socket closed mid-packet")
            rest += chunk
        payload = rest[:length]
        self.buf = rest[length:]
        return json.loads(payload.decode("utf-8"))

    def send(self, command: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self.msg_id += 1
        msg = [0, self.msg_id, command, params or {}]
        raw = json.dumps(msg).encode("utf-8")
        packet = f"{len(raw)}:".encode("utf-8") + raw
        self.sock.sendall(packet)
        while True:
            resp = self._recv_packet()
            if isinstance(resp, list) and len(resp) == 4 and resp[0] == 1 and resp[1] == self.msg_id:
                if resp[2]:
                    raise RuntimeError(f"Marionette error on {command}: {resp[2]}")
                return resp[3]

    def execute_script(self, script: str, args: Optional[List[Any]] = None) -> Any:
        res = self.send("WebDriver:ExecuteScript", {"script": script, "args": args or []})
        return res.get("value") if isinstance(res, dict) and "value" in res else res

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def find_free_port() -> int:
    """Return an available localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
