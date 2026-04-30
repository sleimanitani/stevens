"""Tests for shared.outbound.web — URL validator + WebClient."""

from __future__ import annotations

from typing import List

import httpx
import pytest

from shared.outbound.web import (
    UrlError,
    WebClient,
    validate_url,
)


def _resolver(mapping):
    """Build a resolver callable from a dict {host: [ips]}."""
    return lambda host: list(mapping.get(host, []))


# --- scheme + format validation ---


def test_https_accepted():
    v = validate_url(
        "https://www.example.com/path",
        resolver=_resolver({"www.example.com": ["93.184.216.34"]}),
    )
    assert v.scheme == "https"
    assert v.host == "www.example.com"


def test_http_accepted():
    v = validate_url(
        "http://example.com",
        resolver=_resolver({"example.com": ["93.184.216.34"]}),
    )
    assert v.scheme == "http"


def test_unsupported_scheme_rejected():
    with pytest.raises(UrlError, match="scheme"):
        validate_url("ftp://example.com")
    with pytest.raises(UrlError, match="scheme"):
        validate_url("file:///etc/passwd")
    with pytest.raises(UrlError, match="scheme"):
        validate_url("javascript:alert(1)")


def test_userinfo_in_url_rejected():
    with pytest.raises(UrlError, match="credentials"):
        validate_url("https://user:pass@example.com/")


def test_missing_hostname_rejected():
    with pytest.raises(UrlError, match="hostname"):
        validate_url("https:///path")


def test_empty_url_rejected():
    with pytest.raises(UrlError):
        validate_url("")


# --- private CIDR rejections ---


def test_rfc1918_rejected():
    for ip in ("10.0.0.1", "172.16.0.5", "192.168.1.100"):
        with pytest.raises(UrlError, match="private"):
            validate_url(
                f"https://internal.example.com/",
                resolver=_resolver({"internal.example.com": [ip]}),
            )


def test_loopback_rejected():
    with pytest.raises(UrlError, match="private"):
        validate_url(
            "https://localhost/",
            resolver=_resolver({"localhost": ["127.0.0.1"]}),
        )


def test_link_local_rejected():
    """169.254/16 includes the cloud metadata services."""
    with pytest.raises(UrlError, match="private"):
        validate_url(
            "https://metadata.example.com/",
            resolver=_resolver({"metadata.example.com": ["169.254.169.254"]}),
        )


def test_ipv6_loopback_rejected():
    with pytest.raises(UrlError, match="private"):
        validate_url(
            "https://[::1]/",
        )


def test_ipv6_unique_local_rejected():
    with pytest.raises(UrlError, match="private"):
        validate_url(
            "https://[fc00::1]/",
        )


def test_direct_ip_public_accepted():
    v = validate_url("https://93.184.216.34/")
    assert v.host == "93.184.216.34"


def test_unresolvable_host_rejected():
    with pytest.raises(UrlError, match="resolve"):
        validate_url(
            "https://nonexistent.invalid/",
            resolver=_resolver({}),
        )


def test_dns_rebinding_one_private_one_public_rejected():
    """If even one resolved IP is private, we refuse — defense against
    DNS records that mix public + private answers."""
    with pytest.raises(UrlError, match="private"):
        validate_url(
            "https://attacker.example.com/",
            resolver=_resolver(
                {"attacker.example.com": ["93.184.216.34", "10.0.0.5"]},
            ),
        )


# --- WebClient ---


@pytest.mark.asyncio
async def test_fetch_returns_body_and_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"hello world")

    transport = httpx.MockTransport(handler)
    client = WebClient(transport=transport)
    result = await client.fetch("https://example.com/")
    assert result.status == 200
    assert result.body == b"hello world"


@pytest.mark.asyncio
async def test_fetch_truncates_oversized():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"X" * (60 * 1024 * 1024))

    transport = httpx.MockTransport(handler)
    client = WebClient(transport=transport, max_response_bytes=1024)
    result = await client.fetch("https://example.com/")
    assert len(result.body) == 1024
    assert result.truncated is True


@pytest.mark.asyncio
async def test_same_origin_redirect_followed():
    visited: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append(str(request.url))
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"})
        return httpx.Response(200, content=b"final")

    transport = httpx.MockTransport(handler)
    client = WebClient(transport=transport)
    result = await client.fetch("https://example.com/old")
    assert result.status == 200
    assert result.body == b"final"
    assert any(u.endswith("/new") for u in visited)


@pytest.mark.asyncio
async def test_cross_origin_redirect_not_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        if "evil.com" in str(request.url):
            return httpx.Response(200, content=b"PWNED")
        return httpx.Response(
            302,
            headers={"location": "https://evil.com/x"},
        )

    transport = httpx.MockTransport(handler)
    client = WebClient(transport=transport)
    result = await client.fetch("https://example.com/redir")
    # We surface the redirect response itself, not the destination.
    assert result.status == 302
    assert b"PWNED" not in result.body
