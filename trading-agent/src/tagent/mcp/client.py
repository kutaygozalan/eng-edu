"""Minimal MCP client over streamable HTTP.

Only what a trading agent needs: initialize, list tools, call a tool. No stdio
transport, no sampling, no prompts.

Written directly against the JSON-RPC wire format because the parts of the
official SDK we would use are the parts with the known Robinhood OAuth bug, and
because a broker connection deserves error handling we control: a 401 here must
become AuthExpired, not a generic exception that a retry loop swallows.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from ..brokers.base import AuthExpired, BrokerError
from .tokens import TokenStore, Tokens

PROTOCOL_VERSION = "2025-06-18"
USER_AGENT = "tagent/0.1"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict

    def summary(self, width: int = 90) -> str:
        d = " ".join((self.description or "").split())
        return f"{self.name}: {d[:width]}" if d else self.name


class MCPError(BrokerError):
    pass


class MCPClient:
    """Synchronous MCP client with automatic token refresh.

    `refresher` is injected rather than imported so this class does not depend
    on the OAuth module - it only knows "when the token is stale, call this".
    """

    def __init__(
        self,
        url: str,
        token_store: TokenStore,
        refresher: Callable[[Tokens], Tokens] | None = None,
        timeout: float = 45.0,
    ):
        self.url = url
        self.token_store = token_store
        self.refresher = refresher
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        self._session_id: str | None = None
        self._initialized = False
        self._id = 0

    # ---------------------------------------------------------------- auth --
    def _tokens(self) -> Tokens:
        tokens = self.token_store.load()
        if tokens is None:
            raise AuthExpired(
                "no stored credentials. Run `tagent auth` to authorize."
            )
        if tokens.needs_refresh:
            if not (tokens.refresh_token and self.refresher):
                if tokens.expired:
                    raise AuthExpired(
                        "access token expired and no refresh token is available. "
                        "Run `tagent auth` to re-authorize."
                    )
            else:
                refreshed = self.refresher(tokens)
                # Robinhood invalidates the old refresh token the instant it
                # issues a new one, so this write is not optional and not
                # deferrable: if it fails we have already lost the old one.
                self.token_store.save(refreshed)
                tokens = refreshed
        return tokens

    def _headers(self) -> dict[str, str]:
        tokens = self._tokens()
        h = {"Authorization": f"{tokens.token_type} {tokens.access_token}",
             "MCP-Protocol-Version": PROTOCOL_VERSION}
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    # ------------------------------------------------------------ protocol --
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _rpc(self, method: str, params: dict | None = None, notify: bool = False) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self._next_id()

        try:
            r = self._client.post(self.url, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise MCPError(f"transport error calling {method}: {exc}") from exc

        if r.status_code in (401, 403):
            raise AuthExpired(
                f"MCP server rejected credentials (HTTP {r.status_code}) on "
                f"{method}. Run `tagent auth` to re-authorize."
            )
        if r.status_code == 429:
            retry = r.headers.get("Retry-After", "?")
            raise MCPError(f"rate limited by MCP server (retry after {retry}s)")
        if r.status_code >= 400:
            raise MCPError(f"{method} failed: HTTP {r.status_code} {r.text[:300]}")

        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        if notify or r.status_code == 202 or not r.content:
            return None

        payload = _parse_body(r)
        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"]
            raise MCPError(
                f"{method} returned error {err.get('code')}: {err.get('message')}"
            )
        return payload.get("result") if isinstance(payload, dict) else payload

    def initialize(self) -> dict:
        if self._initialized:
            return {}
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "tagent", "version": "0.1.0"},
            },
        ) or {}
        self._rpc("notifications/initialized", notify=True)
        self._initialized = True
        return result

    # ----------------------------------------------------------- operations --
    def list_tools(self) -> list[ToolSpec]:
        self.initialize()
        tools: list[ToolSpec] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._rpc("tools/list", params) or {}
            for t in result.get("tools", []):
                tools.append(
                    ToolSpec(
                        name=t.get("name", ""),
                        description=t.get("description", "") or "",
                        input_schema=t.get("inputSchema", {}) or {},
                    )
                )
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}) or {}

        if result.get("isError"):
            raise MCPError(f"tool {name} reported an error: {_text_of(result)[:400]}")

        # Prefer structured output when the server provides it; fall back to
        # parsing the text block, which is what most servers actually return.
        if "structuredContent" in result:
            return result["structuredContent"]
        text = _text_of(result)
        if not text:
            return result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MCPClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _parse_body(r: httpx.Response) -> Any:
    """Handle both plain JSON and SSE-framed responses."""
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" not in ctype:
        try:
            return r.json()
        except json.JSONDecodeError as exc:
            raise MCPError(f"response was not JSON: {r.text[:200]}") from exc

    # Streamable HTTP: take the last `data:` payload carrying a JSON-RPC result.
    last: Any = None
    for line in r.text.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                last = json.loads(chunk)
            except json.JSONDecodeError:
                continue
    if last is None:
        raise MCPError("SSE response contained no JSON-RPC payload")
    return last


def _text_of(result: dict) -> str:
    parts = [
        c.get("text", "")
        for c in (result.get("content") or [])
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p)
