"""Envelope-encryption primitives for the secrets module (iron rule 3).

The KEK (key-encryption key) is the ONLY secret living outside Postgres.
Phase 1 sources it from a keyfile whose path comes from RAGZ_KEK_FILE;
KMS/Vault sources arrive in Phase 2+ behind the same load_kek() interface.
"""

import base64
import hashlib
import os
import secrets as _secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ragz.core.errors import SecretsError

KEY_VERSION = 1
_KEK_BYTES = 32
_NONCE_BYTES = 12


def ensure_kek(path: str) -> None:
    """Create a KEK file with 0600 permissions if missing (bootstrap path)."""
    p = Path(path)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.urlsafe_b64encode(_secrets.token_bytes(_KEK_BYTES)))
    except FileExistsError:
        # Another process won the race; key already exists
        return


def load_kek(path: str) -> bytes:
    p = Path(path)
    if not p.exists():
        raise SecretsError("KEK file missing; run `python -m ragz.bootstrap` first")
    kek = base64.urlsafe_b64decode(p.read_bytes())
    if len(kek) != _KEK_BYTES:
        raise SecretsError("KEK file corrupt: expected 32 key bytes")
    return kek


def encrypt(kek: bytes, plaintext: str) -> tuple[bytes, bytes]:
    """Return (nonce, ciphertext) under AES-256-GCM."""
    if len(kek) != _KEK_BYTES:
        raise SecretsError("invalid KEK length")
    nonce = _secrets.token_bytes(_NONCE_BYTES)
    return nonce, AESGCM(kek).encrypt(nonce, plaintext.encode(), None)


def decrypt(kek: bytes, nonce: bytes, ciphertext: bytes) -> str:
    if len(kek) != _KEK_BYTES:
        raise SecretsError("invalid KEK length")
    try:
        return AESGCM(kek).decrypt(nonce, ciphertext, None).decode()
    except InvalidTag as exc:
        raise SecretsError("secret decryption failed (wrong or rotated KEK)") from exc


def fingerprint(value: str) -> str:
    """Display-safe identifier: last 4 chars + truncated SHA-256. Never log the value."""
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    suffix = value[-4:] if len(value) > 8 else "????"
    return f"...{suffix} sha256:{digest}"
