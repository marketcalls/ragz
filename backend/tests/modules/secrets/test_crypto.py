import re
import stat
from pathlib import Path

import pytest

from ragz.core.errors import SecretsError
from ragz.modules.secrets.crypto import (
    decrypt,
    encrypt,
    ensure_kek,
    fingerprint,
    load_kek,
)


def test_ensure_kek_creates_0600_and_is_idempotent(tmp_path: Path) -> None:
    path = str(tmp_path / "kek")
    ensure_kek(path)
    mode = stat.S_IMODE(Path(path).stat().st_mode)
    assert mode == 0o600
    first = Path(path).read_bytes()
    ensure_kek(path)  # second call must not rotate the key
    assert Path(path).read_bytes() == first
    assert len(load_kek(path)) == 32


def test_load_missing_kek_raises(tmp_path: Path) -> None:
    with pytest.raises(SecretsError):
        load_kek(str(tmp_path / "nope"))


def test_encrypt_decrypt_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "kek")
    ensure_kek(path)
    kek = load_kek(path)
    nonce, ciphertext = encrypt(kek, "sk-super-secret-value")
    assert b"sk-super-secret-value" not in ciphertext
    assert decrypt(kek, nonce, ciphertext) == "sk-super-secret-value"


def test_wrong_kek_fails_closed(tmp_path: Path) -> None:
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    ensure_kek(a)
    ensure_kek(b)
    nonce, ciphertext = encrypt(load_kek(a), "value")
    with pytest.raises(SecretsError):
        decrypt(load_kek(b), nonce, ciphertext)


def test_fingerprint_format_and_no_leak() -> None:
    fp = fingerprint("sk-abcdef1234567890wxyz")
    assert re.fullmatch(r"\.\.\.wxyz sha256:[0-9a-f]{12}", fp)
    assert "sk-abcdef1234567890" not in fp


def test_ensure_kek_race_condition(tmp_path: Path) -> None:
    """Calling ensure_kek twice must not raise (second call wins race gracefully)."""
    path = str(tmp_path / "kek")
    ensure_kek(path)
    # Second call should return normally without exception
    ensure_kek(path)
    assert Path(path).exists()


def test_ensure_kek_race_pre_created_file(tmp_path: Path) -> None:
    """Pre-creating the file should not cause ensure_kek to raise FileExistsError."""
    path = str(tmp_path / "kek")
    tmp_path.mkdir(exist_ok=True)
    Path(path).write_bytes(b"fake_kek_content")
    # Should not raise even though file exists
    ensure_kek(path)
    assert Path(path).exists()


def test_fingerprint_short_secret_reversibility() -> None:
    """Short secrets should not leak any characters."""
    fp = fingerprint("abcd")
    assert "abcd" not in fp
    assert re.fullmatch(r"\.\.\..... sha256:[0-9a-f]{12}", fp)


def test_decrypt_with_invalid_kek_length() -> None:
    """decrypt() should reject KEK that is not exactly 32 bytes."""
    kek_16 = b"x" * 16
    with pytest.raises(SecretsError, match="invalid KEK length"):
        decrypt(kek_16, b"x" * 12, b"x" * 32)


def test_encrypt_with_invalid_kek_length() -> None:
    """encrypt() should reject KEK that is not exactly 32 bytes."""
    kek_16 = b"x" * 16
    with pytest.raises(SecretsError, match="invalid KEK length"):
        encrypt(kek_16, "plaintext")
