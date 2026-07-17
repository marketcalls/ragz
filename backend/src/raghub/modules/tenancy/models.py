from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base, UUIDPk


class Organization(UUIDPk, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(unique=True)


class Workspace(UUIDPk, Base):
    __tablename__ = "workspaces"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str]
    embedding_model: Mapped[str] = mapped_column(default="bge-m3")
    min_score: Mapped[float] = mapped_column(default=0.35)
    default_model_id: Mapped[UUID | None] = mapped_column(default=None)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(default="member")
