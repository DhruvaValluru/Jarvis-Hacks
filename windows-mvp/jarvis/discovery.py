"""Find the other Jarvis PCs automatically: no IP addresses to type.

A serving PC answers UDP probes on port 8766. The PC you talk to sends a probe as a
LAN broadcast and straight to each online Tailscale peer (broadcasts don't cross
Tailscale). Both sides prove they hold the same JARVIS_TOKEN with an HMAC, so a
stranger on the network can't find or impersonate your PCs.
"""

import hashlib
import hmac
import json
import secrets
import socket
import subprocess
import threading
import time
from typing import Callable

PORT = 8766


def _mac(token: str, *parts: str) -> str:
    return hmac.new(token.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()[:32]


class Responder:
    """Answers probes from PCs that share our token (runs on every serving PC)."""

    def __init__(self, name: str, http_port: int, token: str, port: int = PORT, log: Callable[[str], None] = print):
        self.name, self.http_port, self.token = name, http_port, token
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("", port))
        except OSError as e:
            log(f"Auto-discovery is off (UDP port {port} is busy: {e}). List this PC in JARVIS_DEVICES instead.")
            self.sock.close()
            return
        threading.Thread(target=self._run, daemon=True, name="jarvis-discovery").start()

    def _run(self) -> None:
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
                msg = json.loads(data)
                nonce = str(msg["nonce"])
                if msg.get("jarvis") != "probe" or not hmac.compare_digest(
                    str(msg.get("mac", "")), _mac(self.token, "probe", nonce)
                ):
                    continue
                reply = {
                    "jarvis": "here",
                    "name": self.name,
                    "port": self.http_port,
                    "mac": _mac(self.token, "here", nonce, self.name, str(self.http_port)),
                }
                self.sock.sendto(json.dumps(reply).encode(), addr)
            except (OSError, ValueError, KeyError, TypeError):
                continue


def probe(token: str, targets: list[tuple[str, int]] | None = None, timeout: float = 0.6) -> list[tuple[str, str, int]]:
    """Ask who's out there. Returns [(name, ip, http_port)] for PCs with the same token."""
    if targets is None:
        targets = [("255.255.255.255", PORT)] + [(ip, PORT) for ip in tailscale_peers()]
    nonce = secrets.token_hex(8)
    msg = json.dumps({"jarvis": "probe", "nonce": nonce, "mac": _mac(token, "probe", nonce)}).encode()
    found: dict[str, tuple[str, str, int]] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in targets:
            try:
                s.sendto(msg, target)
            except OSError:
                pass
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            s.settimeout(left)
            try:
                data, (ip, _) = s.recvfrom(2048)
                r = json.loads(data)
                name, port = str(r["name"]), int(r["port"])
                if r.get("jarvis") == "here" and hmac.compare_digest(
                    str(r.get("mac", "")), _mac(token, "here", nonce, name, str(port))
                ):
                    found[name] = (name, ip, port)
            except socket.timeout:
                break
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return list(found.values())


def tailscale_peers() -> list[str]:
    """IPv4 addresses of the online machines on your Tailscale network (empty without Tailscale)."""
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        peers = json.loads(out.stdout).get("Peer") or {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    ips = []
    for p in peers.values():
        if p.get("Online"):
            ips += [ip for ip in p.get("TailscaleIPs") or [] if "." in ip][:1]
    return ips
