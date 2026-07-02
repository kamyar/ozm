#!/usr/bin/env python3
"""Unix domain socket client for communicating with the ozm menu bar app."""

import json
import os
import socket
import stat

SOCKET_PATH = os.path.expanduser("~/.ozm/ozm.sock")
DEFAULT_TIMEOUT = 300.0


def _socket_is_trusted(path: str) -> bool:
    """Only talk to a socket that could have been created by the real ozm app.

    A rogue local process could pre-create ~/.ozm/ozm.sock and answer
    approval requests itself. Require the path to be a real socket (not a
    symlink), owned by the current user, and inaccessible to group/other.
    """
    try:
        if os.path.islink(path):
            return False
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISSOCK(st.st_mode):
        return False
    if st.st_uid != os.geteuid():
        return False
    if stat.S_IMODE(st.st_mode) & 0o022:
        return False
    return True


def send_request(request: dict, timeout: float = DEFAULT_TIMEOUT) -> dict | None:
    """Send a JSON request to the ozm app and return the parsed response.

    Returns None on any failure (socket missing, untrusted, refused,
    timeout, bad JSON).
    """
    if not _socket_is_trusted(SOCKET_PATH):
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
