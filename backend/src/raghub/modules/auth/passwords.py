from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()  # argon2id defaults


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(hashed: str, raw: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except VerifyMismatchError:
        return False
