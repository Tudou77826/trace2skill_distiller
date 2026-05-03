"""Custom httpx transports — proxy bypass routing."""

from __future__ import annotations

import re

import httpx


class ProxyBypassTransport(httpx.BaseTransport):
    """Dual-transport router: matched hosts bypass proxy, all others use proxy.

    Usage:
        transport = ProxyBypassTransport(
            proxy="socks5://127.0.0.1:1080",
            bypass_patterns="localhost,127\\.0\\.0\\.1,.*\\.internal\\.com",
            verify=False,
        )
        client = httpx.Client(transport=transport)
    """

    def __init__(
        self,
        proxy: str,
        bypass_patterns: str,
        verify: bool = False,
    ) -> None:
        self._proxy_transport = httpx.HTTPTransport(proxy=proxy, verify=verify)
        self._direct_transport = httpx.HTTPTransport(verify=verify)
        bypass_list: list[re.Pattern[str]] = []
        for p in bypass_patterns.split(","):
            p = p.strip()
            if p:
                try:
                    bypass_list.append(re.compile(p))
                except re.error as e:
                    raise ValueError(
                        f"Invalid regex in proxy_bypass pattern '{p}': {e}"
                    ) from e
        self._bypass = bypass_list

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if any(p.search(host) for p in self._bypass):
            return self._direct_transport.handle_request(request)
        return self._proxy_transport.handle_request(request)

    def close(self) -> None:
        self._proxy_transport.close()
        self._direct_transport.close()
