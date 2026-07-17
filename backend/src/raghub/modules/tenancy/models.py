from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base, UUIDPk


class Organization(UUIDPk, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(unique=True)
