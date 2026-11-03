"""Fetching the picture a site gives for a person, without being turned
against the network the server sits in.

The address comes from the site's token, so it is checked like any input
from outside: https only, a host whose every address is public (not the
server itself, not the private network, not the cloud's metadata service),
and the connection goes to the address that was checked, so a name that
changes what it points to between the check and the request gains nothing.
Redirects are followed a few times, each checked the same way. The answer
is read up to a size limit and then goes through the same processing as an
uploaded photo.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.users import avatars

TIMEOUT = 8
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3


class PictureError(Exception):
    pass


def public_address(host: str, port: int) -> str:
    """One public IP address of the host, or PictureError."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise PictureError(f"cannot resolve {host}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise PictureError(f"no address for {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise PictureError(f"{host} points to a non-public address")
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


def download(url: str) -> bytes:
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise PictureError("only https addresses are fetched")
        port = parts.port or 443
        address = public_address(parts.hostname, port)
        connection = _PinnedHTTPSConnection(
            parts.hostname, address, port=port, timeout=TIMEOUT, context=ssl.create_default_context()
        )
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            connection.request("GET", path, headers={"User-Agent": "QQplay picture fetcher", "Accept": "image/*"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location") or ""
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise PictureError(f"HTTP {response.status}")
            data = response.read(MAX_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise PictureError(str(exc)) from exc
        finally:
            connection.close()
        if len(data) > MAX_BYTES:
            raise PictureError("the picture is too large")
        return data
    raise PictureError("too many redirects")


def apply(identity) -> bool:
    """Show the site's picture for the person, or their initials when the
    site gives none or it cannot be used. True if a picture was stored."""
    user = identity.user
    url = identity.picture_url
    if not url:
        if user.avatar_photo or user.avatar:
            avatars.remove_photo(user)
            if user.avatar:
                user.avatar = ""
                user.save(update_fields=["avatar"])
        return False
    data = download(url)
    upload = SimpleUploadedFile("picture", data, content_type="image/*")
    avatars.set_photo(user, upload)
    return True
