from ragz.modules.auth.passwords import hash_password, verify_password


def test_hash_and_verify() -> None:
    h = hash_password("s3cret!")
    assert h != "s3cret!"
    assert h.startswith("$argon2id$")
    assert verify_password(h, "s3cret!")
    assert not verify_password(h, "wrong")


def test_verify_invalid_hash_returns_false() -> None:
    assert not verify_password("not-a-hash", "x")
