"""OAuth 2.1 authorization-code flow with PKCE, for MCP servers.

Written by hand rather than borrowed because the borrowed one is what breaks:
Claude Code's native MCP OAuth persists an EMPTY access token against Robinhood
(anthropics/claude-code#65895) - the browser leg succeeds, the exchange silently
yields nothing usable. So this module treats a token response that parses fine
but carries no access_token as a hard error, which is precisely the case that
bug lets through.

Headless is the primary path, not an afterthought: you get a URL, you approve it
in whatever browser you like, you paste the redirect back. No browser is ever
required on the box running the agent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from dataclasses import dataclass

import httpx

from .tokens import Tokens

# Loopback redirect. Registered as a native/public client; the port is only used
# when you run the flow on a machine that does have a browser.
DEFAULT_REDIRECT = "http://localhost:8765/callback"
USER_AGENT = "tagent/0.1 (+https://github.com/kutaygozalan/eng-edu)"


class OAuthError(RuntimeError):
    pass


@dataclass
class ServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    scopes_supported: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict) -> "ServerMetadata":
        missing = [k for k in ("authorization_endpoint", "token_endpoint") if k not in d]
        if missing:
            raise OAuthError(f"authorization server metadata missing {missing}")
        return cls(
            authorization_endpoint=d["authorization_endpoint"],
            token_endpoint=d["token_endpoint"],
            registration_endpoint=d.get("registration_endpoint"),
            scopes_supported=tuple(d.get("scopes_supported") or ()),
        )


def _pkce() -> tuple[str, str]:
    """(verifier, challenge) - S256 only. `plain` is not acceptable here."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def discover(resource_url: str, client: httpx.Client | None = None) -> ServerMetadata:
    """Find the authorization server for an MCP resource.

    Tries the RFC 9728 protected-resource document first (which is how an MCP
    server names its issuer), then falls back to well-known paths on the
    resource's own origin.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT})
    try:
        parsed = urllib.parse.urlparse(resource_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        issuers: list[str] = []
        try:
            r = client.get(f"{origin}/.well-known/oauth-protected-resource")
            if r.status_code == 200:
                issuers = list(r.json().get("authorization_servers") or [])
        except httpx.HTTPError:
            pass

        candidates: list[str] = []
        for issuer in issuers:
            issuer = issuer.rstrip("/")
            candidates += [
                f"{issuer}/.well-known/oauth-authorization-server",
                f"{issuer}/.well-known/openid-configuration",
            ]
        candidates += [
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        ]

        errors: list[str] = []
        for url in candidates:
            try:
                r = client.get(url)
            except httpx.HTTPError as exc:
                errors.append(f"{url}: {exc}")
                continue
            if r.status_code == 200:
                return ServerMetadata.from_dict(r.json())
            errors.append(f"{url}: HTTP {r.status_code}")

        raise OAuthError(
            "could not discover OAuth metadata for "
            f"{resource_url}. Tried:\n  " + "\n  ".join(errors)
        )
    finally:
        if owns_client:
            client.close()


def register_client(
    meta: ServerMetadata,
    redirect_uri: str = DEFAULT_REDIRECT,
    client_name: str = "tagent",
    client: httpx.Client | None = None,
) -> tuple[str, str | None]:
    """Dynamic client registration (RFC 7591). Returns (client_id, client_secret)."""
    if not meta.registration_endpoint:
        raise OAuthError(
            "server does not advertise dynamic registration; supply a client_id "
            "from the provider's developer console instead"
        )
    owns_client = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})
    try:
        r = client.post(
            meta.registration_endpoint,
            json={
                "client_name": client_name,
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",  # public client + PKCE
            },
        )
        if r.status_code not in (200, 201):
            raise OAuthError(f"registration failed: HTTP {r.status_code} {r.text[:400]}")
        data = r.json()
        cid = data.get("client_id")
        if not cid:
            raise OAuthError(f"registration response had no client_id: {data}")
        return cid, data.get("client_secret")
    finally:
        if owns_client:
            client.close()


@dataclass
class PendingAuth:
    """The half-finished flow, held between printing a URL and pasting a code."""

    authorize_url: str
    state: str
    verifier: str
    client_id: str
    client_secret: str | None
    token_endpoint: str
    redirect_uri: str


def begin(
    meta: ServerMetadata,
    client_id: str,
    client_secret: str | None = None,
    redirect_uri: str = DEFAULT_REDIRECT,
    scopes: tuple[str, ...] = (),
) -> PendingAuth:
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    scope = " ".join(scopes or meta.scopes_supported)
    if scope:
        params["scope"] = scope
    return PendingAuth(
        authorize_url=f"{meta.authorization_endpoint}?{urllib.parse.urlencode(params)}",
        state=state,
        verifier=verifier,
        client_id=client_id,
        client_secret=client_secret,
        token_endpoint=meta.token_endpoint,
        redirect_uri=redirect_uri,
    )


def extract_code(redirect_response: str, expected_state: str) -> str:
    """Pull ?code= out of the pasted redirect URL, verifying state.

    The callback page shows a browser error - there is nothing listening on
    localhost when you run headless. That is expected; the URL bar still holds
    everything we need.
    """
    text = redirect_response.strip()
    if not text:
        raise OAuthError("empty redirect URL")

    qs = urllib.parse.parse_qs(urllib.parse.urlparse(text).query) if "?" in text else {}
    if not qs and "=" in text:
        qs = urllib.parse.parse_qs(text.lstrip("?"))

    if "error" in qs:
        desc = qs.get("error_description", [""])[0]
        raise OAuthError(f"authorization denied: {qs['error'][0]} {desc}".strip())

    state = qs.get("state", [None])[0]
    if state != expected_state:
        # A mismatched state means this response belongs to a different flow.
        raise OAuthError("state mismatch - restart the authorization flow")

    code = qs.get("code", [None])[0]
    if not code:
        raise OAuthError(f"no authorization code in redirect URL: {text[:200]}")
    return code


def exchange(pending: PendingAuth, code: str, client: httpx.Client | None = None) -> Tokens:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending.redirect_uri,
        "client_id": pending.client_id,
        "code_verifier": pending.verifier,
    }
    if pending.client_secret:
        data["client_secret"] = pending.client_secret
    return _token_request(pending.token_endpoint, data, client)


def refresh(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str | None = None,
    client: httpx.Client | None = None,
) -> Tokens:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    return _token_request(token_endpoint, data, client)


def _token_request(
    endpoint: str, data: dict, client: httpx.Client | None = None
) -> Tokens:
    owns_client = client is None
    client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    try:
        r = client.post(
            endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            raise OAuthError(f"token endpoint returned HTTP {r.status_code}: {r.text[:400]}")
        try:
            payload = r.json()
        except json.JSONDecodeError as exc:
            raise OAuthError(f"token response was not JSON: {r.text[:200]}") from exc

        access = payload.get("access_token")
        # This is the claude-code#65895 failure: a 200 with a well-formed body
        # and nothing usable in it. Persisting that produces an agent that
        # authenticates "successfully" and then 401s on every call.
        if not access or not str(access).strip():
            raise OAuthError(
                "token endpoint returned HTTP 200 with an empty access_token "
                f"(keys: {sorted(payload)}). Refusing to persist an unusable "
                "token; re-run authorization."
            )

        expires_in = payload.get("expires_in")
        try:
            expires_in = float(expires_in) if expires_in is not None else 3600.0
        except (TypeError, ValueError):
            expires_in = 3600.0

        return Tokens(
            access_token=str(access),
            refresh_token=payload.get("refresh_token") or data.get("refresh_token"),
            expires_at=time.time() + expires_in,
            token_type=payload.get("token_type", "Bearer"),
            scope=payload.get("scope"),
        )
    finally:
        if owns_client:
            client.close()
