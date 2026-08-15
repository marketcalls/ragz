"""Envelope-encryption primitives for the secrets module (iron rule 3).

The AES-256-GCM implementation was moved DOWN to `ragz.core.crypto` so that
`core/app_settings` can encrypt the JWT signing key at rest under the same KEK
(RAGZ-PUB-07) without `ragz.core` importing `ragz.modules` (a layered-import
violation). This module re-exports those names verbatim so every existing
`ragz.modules.secrets.crypto` import keeps working and there is still exactly
ONE encrypt/decrypt implementation in the codebase.
"""

from ragz.core.crypto import (
    KEY_VERSION,
    decrypt,
    encrypt,
    ensure_kek,
    fingerprint,
    load_kek,
)

__all__ = [
    "KEY_VERSION",
    "decrypt",
    "encrypt",
    "ensure_kek",
    "fingerprint",
    "load_kek",
]
