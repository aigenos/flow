"""Credential envelopes and environment references.

No method in this module exposes a secret-bearing representation suitable for
an API response. Callers resolve credentials only immediately before provider
use.
"""
from __future__ import annotations

import base64
import hashlib
import os


class SecretResolver:
    PREFIX = "aesgcm:v1:"

    def __init__(self, master_key: str | None = None) -> None:
        self._configured_key = master_key

    def _key(self) -> bytes:
        value = self._configured_key or os.getenv("FG_CONNECTOR_MASTER_KEY", "")
        if not value:
            raise RuntimeError(
                "FG_CONNECTOR_MASTER_KEY is required for inline connector credentials"
            )
        # Accept an explicit 32-byte urlsafe-base64/hex key. A passphrase is
        # deterministically stretched to 256 bits for deployment ergonomics.
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        try:
            decoded = bytes.fromhex(value)
            if len(decoded) == 32:
                return decoded
        except ValueError:
            pass
        return hashlib.sha256(value.encode("utf-8")).digest()

    def encrypt(self, value: str) -> str:
        if not value:
            raise ValueError("credential URI must not be empty")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise RuntimeError("inline credentials require the connectors extra") from exc
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key()).encrypt(nonce, value.encode("utf-8"), b"aigenos-flow")
        return self.PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, envelope: str) -> str:
        if not envelope.startswith(self.PREFIX):
            raise ValueError("unsupported connector credential envelope")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.urlsafe_b64decode(envelope.removeprefix(self.PREFIX))
        if len(raw) < 29:
            raise ValueError("invalid connector credential envelope")
        plain = AESGCM(self._key()).decrypt(raw[:12], raw[12:], b"aigenos-flow")
        return plain.decode("utf-8")

    def resolve(self, *, encrypted: str | None, env_reference: str | None) -> str:
        if env_reference:
            value = os.getenv(env_reference)
            if not value:
                raise RuntimeError("connector credential environment reference is not set")
            return value
        if encrypted:
            return self.decrypt(encrypted)
        raise RuntimeError("connector has no configured credential")

    @staticmethod
    def parse_input(value: str) -> tuple[str, str]:
        """Return ``(mode, payload)`` for an inline URI or ``env:NAME``."""
        if value.startswith("env:"):
            name = value[4:]
            if not name or not name.replace("_", "A").isalnum() or name[0].isdigit():
                raise ValueError("environment credential must use env:VARIABLE_NAME")
            return "environment", name
        return "inline", value
