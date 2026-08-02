from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk
from ragz.modules.secrets.crypto import KEY_VERSION


class Secret(UUIDPk, Base):
    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(unique=True, index=True)
    ciphertext: Mapped[bytes]
    nonce: Mapped[bytes]
    key_version: Mapped[int] = mapped_column(default=KEY_VERSION)
    fingerprint: Mapped[str]
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
