#!/usr/bin/env python3
"""Unix domain socket client for communicating with the ozm menu bar app."""

import json
import os
import socket

SOCKET_PATH = os.path.expanduser("~/.ozm/ozm.sock")
DEFAULT_TIMEOUT = 300.0


def send_request(request: dict, timeout: float = DEFAULT_TIMEOUT) -> dict | None:
    """Send a JSON request to the ozm app and return the parsed response.

    Returns None on any failure (socket missing, refused, timeout, bad JSON).
    """
    if not os.path.exists(SOCKET_PATH):
        return None

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(SOCKET_PATH)
        payload = json.dumps(request, ensure_ascii=False) + "\n"
        sock.sendall(payload.encode("utf-8"))

        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break

        data = b"".join(chunks).decode("utf-8").strip()
        if not data:
            return None
        return json.loads(data)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, socket.timeout):
        return None
    finally:
        sock.close()
