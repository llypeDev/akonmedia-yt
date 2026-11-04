"""Requests to addresses a site gave us (its pictures, its webhook), without
being turned against the network the server sits in.

https only; a host whose every address is public (not the server itself, not
the private network, not the cloud's metadata service); the connection goes
to the address that was checked, so a name that changes what it points to
between the check and the request gains nothing; redirects are followed a
few times, each checked the same way; answers are read up to a limit.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

TIMEOUT = 8
MAX_REDIRECTS = 3


class NetError(Exception):
    pass


@dataclass
class Answer:
    status: int
    body: bytes


def public_address(host: str, port: int) -> str:
    """One public IP address of the host, or NetError."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise NetError(f"cannot resolve {host}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise NetError(f"no address for {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise NetError(f"{host} points to a non-public address")
    return sorted(addresses)[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to an address checked beforehand, still verifying the
    certificate against the host name."""

    def __init__(self, host, address, **kwargs):
        super().__init__(host, **kwargs)
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def request(method: str, url: str, body: bytes | None = None, headers: dict | None = None,
            max_bytes: int = 1024 * 1024, follow: bool = True) -> Answer:
    headers = {"User-Agent": "QQplay", **(headers or {})}
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise NetError("only https addresses are used")
        port = parts.port or 443
        address = public_address(parts.hostname, port)
        connection = _PinnedHTTPSConnection(
            parts.hostname, address, port=port, timeout=TIMEOUT, context=ssl.create_default_context()
        )
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if follow and response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader("Location") or "")
                if response.status == 303:
                    method, body = "GET", None
                continue
            data = response.read(max_bytes + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise NetError(str(exc) or type(exc).__name__) from exc
        finally:
            connection.close()
        if len(data) > max_bytes:
            raise NetError("the answer is too large")
        return Answer(response.status, data)
    raise NetError("too many redirects")
