"""WebClient + URL validator.

The validator is the load-bearing security primitive: it rejects URLs
pointing at private networks (RFC-1918 + link-local + loopback + IPv6
ULA + IPv6 link-local + IPv6 loopback). Stevens's outbound never reaches
the local LAN, the host's own services, or any cloud-internal metadata
service.

The check resolves the hostname before issuing the request and refuses if
any A/AAAA record is in a private range. Hostname-only checks aren't
enough — an attacker who controls a public DNS name could point it at
``169.254.169.254`` (the AWS metadata service) and bypass us.

WebClient itself is a thin async httpx wrapper that enforces:
- 50 MiB response cap
- 30s default timeout
- No cross-domain redirects (same-origin redirects allowed up to a depth)
- Scheme allow-list: ``http``, ``https``
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx


# 50 MiB cap on response body.
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_REDIRECT_DEPTH = 5


class UrlError(Exception):
    """Raised when a URL is rejected by the validator."""


@dataclass(frozen=True)
class ValidatedUrl:
    """A URL that passed validation. Holds the original URL + resolved IPs."""

    url: str
    scheme: str
    host: str
    resolved_ips: List[str] = field(default_factory=list)


def _ip_is_private(ip_str: str) -> bool:
    """True if the IP is in a private / loopback / link-local / unique-local range."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable — refuse
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> List[str]:
    """Resolve ``host`` to all A/AAAA records. Empty list if resolution fails."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    out: List[str] = []
    for entry in infos:
        addr = entry[4][0]
        if addr not in out:
            out.append(addr)
    return out


def validate_url(
    url: str,
    *,
    resolver: Callable[[str], List[str]] = _resolve_host,
    allow_schemes: tuple = ("https", "http"),
) -> ValidatedUrl:
    """Validate ``url`` for outbound use.

    Raises :class:`UrlError` on:
    - non-allowed scheme
    - URL with userinfo (credentials embedded)
    - missing hostname
    - hostname that resolves to a private/loopback/link-local IP
    - hostname that fails to resolve
    """
    if not isinstance(url, str) or not url:
        raise UrlError("url must be a non-empty string")
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in allow_schemes:
        raise UrlError(f"scheme {scheme!r} not in allow-list {allow_schemes}")
    if parsed.username or parsed.password:
        raise UrlError("urls with embedded credentials are rejected")
    host = (parsed.hostname or "").strip()
    if not host:
        raise UrlError("url has no hostname")
    # If host is itself an IP, check directly without resolving.
    try:
        ip = ipaddress.ip_address(host)
        if _ip_is_private(str(ip)):
            raise UrlError(f"host {host!r} resolves to a private address")
        resolved = [str(ip)]
    except ValueError:
        resolved = resolver(host)
        if not resolved:
            raise UrlError(f"could not resolve host {host!r}")
        for ip_str in resolved:
            if _ip_is_private(ip_str):
                raise UrlError(
                    f"host {host!r} resolves to private address {ip_str}"
                )
    return ValidatedUrl(url=url, scheme=scheme, host=host, resolved_ips=resolved)


def _is_same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)


@dataclass(frozen=True)
class FetchResult:
    status: int
    headers: Dict[str, str]
    body: bytes
    final_url: str
    truncated: bool = False


class WebClient:
    """Async httpx-backed client. Enforces caps + redirect policy."""

    def __init__(
        self,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_redirect_depth: int = MAX_REDIRECT_DEPTH,
    ) -> None:
        self._transport = transport
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._max_redirects = max_redirect_depth

    async def fetch(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        follow_redirects: bool = True,
    ) -> FetchResult:
        """GET ``url``. URL is assumed already-validated by the caller.

        Same-origin redirects are followed up to ``max_redirect_depth``;
        cross-origin redirects abort with the redirect response surfaced.
        """
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,  # we handle redirects ourselves to enforce same-origin
        ) as client:
            current_url = url
            for _ in range(self._max_redirects + 1):
                response = await client.get(current_url, headers=headers or {})
                if response.is_redirect and follow_redirects:
                    next_url = str(response.headers.get("location", ""))
                    if not next_url:
                        break
                    # Resolve relative redirect locations against current_url.
                    if next_url.startswith("/"):
                        parsed = urlparse(current_url)
                        next_url = urlunparse(
                            (parsed.scheme, parsed.netloc, next_url, "", "", "")
                        )
                    if not _is_same_origin(current_url, next_url):
                        # Cross-origin redirect: don't follow. Surface the
                        # redirect response itself.
                        break
                    current_url = next_url
                    continue
                # Not a redirect (or follow_redirects=False).
                break

            body = response.content
            truncated = False
            if len(body) > self._max_bytes:
                body = body[: self._max_bytes]
                truncated = True
            return FetchResult(
                status=response.status_code,
                headers={k: v for k, v in response.headers.items()},
                body=body,
                final_url=str(response.request.url),
                truncated=truncated,
            )
