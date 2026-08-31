"""OAuth token storage.

Two backends, both holding the same contract: tokens go in encrypted at rest and
come out decrypted, and a refresh REWRITES the file in place.

That last part is the one that bites people. Robinhood issues single-use refresh
tokens and invalidates the old one immediately, so if the token file is mounted
read-only the first refresh silently destroys your session: the server has moved
on, and the copy on disk is already dead. `EncryptedFileTokenStore.save` fails
loudly rather than letting that happen quietly.
"""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Refresh this far ahead of the stated expiry. Robinhood's own tooling refreshes
# well ahead of time; a cycle that starts 30s before expiry must not race it.
REFRESH_SKEW_SECONDS = 24 * 3600


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: float          # epoch seconds
    token_type: str = "Bearer"
    scope: str | None = None

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        return time.time() >= (self.expires_at - REFRESH_SKEW_SECONDS)

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.expires_at - time.time())


class TokenStore(Protocol):
    def load(self) -> Tokens | None: ...
    def save(self, tokens: Tokens) -> None: ...
    def clear(self) -> None: ...


class EncryptedFileTokenStore:
    """AES-256-GCM token file. The right choice for Docker and headless VMs.

    The key comes from the environment, never the file: an attacker who gets the
    volume without the env var gets ciphertext.
    """

    def __init__(self, path: str | Path, key_env: str = "TAGENT_TOKEN_KEY"):
        self.path = Path(path).expanduser()
        self.key_env = key_env

    def _key(self) -> bytes:
        raw = os.environ.get(self.key_env)
        if not raw:
            raise RuntimeError(
                f"{self.key_env} is not set. Generate one with:\n"
                "  python3 -c \"import os,base64; "
                "print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
            )
        import base64

        try:
            key = base64.urlsafe_b64decode(raw)
        except Exception as exc:
            raise RuntimeError(f"{self.key_env} is not valid base64") from exc
        if len(key) != 32:
            raise RuntimeError(f"{self.key_env} must decode to 32 bytes, got {len(key)}")
        return key

    def load(self) -> Tokens | None:
        if not self.path.exists():
            return None
        blob = self.path.read_bytes()
        if len(blob) < 13:
            raise RuntimeError(f"token file {self.path} is truncated")
        nonce, ct = blob[:12], blob[12:]
        try:
            plain = AESGCM(self._key()).decrypt(nonce, ct, None)
        except Exception as exc:
            raise RuntimeError(
                f"could not decrypt {self.path} - wrong {self.key_env}?"
            ) from exc
        return Tokens(**json.loads(plain))

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        nonce = os.urandom(12)
        ct = AESGCM(self._key()).encrypt(nonce, json.dumps(asdict(tokens)).encode(), None)

        # Atomic replace so a crash mid-write cannot leave a half-written file
        # that decrypts to nothing.
        #
        # The write is ATTEMPTED rather than pre-checked with os.access, which
        # reports success for root and for read-only bind mounts alike. Since a
        # Robinhood refresh token is single-use and the old one is dead the
        # moment a new one is issued, a silently failed write ends the session -
        # so this failure has to be loud and has to name the cause.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_bytes(nonce + ct)
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)   # 0600
            os.replace(tmp, self.path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"could not write token file {self.path}: {exc}. Refresh tokens "
                "are single-use, so a read-only path ends the session on the "
                "first refresh. Mount it read-write and re-run `tagent auth`."
            ) from exc

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class MemoryTokenStore:
    """For tests. Never persists."""

    def __init__(self, tokens: Tokens | None = None):
        self._tokens = tokens

    def load(self) -> Tokens | None:
        return self._tokens

    def save(self, tokens: Tokens) -> None:
        self._tokens = tokens

    def clear(self) -> None:
        self._tokens = None
