"""per-workspace chunk_method + per-document chunk_method_override

Revision ID: b45b119d8e97
Revises: 43a13912a44c
Create Date: 2026-08-15 00:00:00.000000

Plan (chunk methods + editor) Task 2: `workspaces.chunk_method` picks the
default chunking strategy for the workspace's ingests (server_default
'heading' so every pre-existing workspace keeps today's behavior byte-
identical); `documents.chunk_method_override` lets a single document opt out
of the workspace default (NULL = inherit). Both are CHECK-constrained to the
same closed set the pipeline's `chunk_document` dispatcher understands.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b45b119d8e97'
down_revision: Union[str, Sequence[str], None] = '43a13912a44c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_METHODS = "'heading','fixed','page','table_qa'"


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("chunk_method", sa.String(), nullable=False, server_default="heading"),
    )
    op.create_check_constraint(
        "ck_workspaces_chunk_method", "workspaces", f"chunk_method IN ({_METHODS})",
    )
    op.add_column(
        "documents",
        sa.Column("chunk_method_override", sa.String(), nullable=True),
    )
    op.create_check_constraint(
        "ck_documents_chunk_method_override", "documents",
        f"chunk_method_override IS NULL OR chunk_method_override IN ({_METHODS})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_documents_chunk_method_override", "documents", type_="check")
    op.drop_column("documents", "chunk_method_override")
    op.drop_constraint("ck_workspaces_chunk_method", "workspaces", type_="check")
    op.drop_column("workspaces", "chunk_method")
