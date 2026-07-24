"""model modality, dimension, collection_name + seed local embedding model

Revision ID: d1e8f4a2b6c3
Revises: fcf2710fd015
Create Date: 2026-07-24 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from raghub.core.config import get_settings

# revision identifiers, used by Alembic.
revision: str = 'd1e8f4a2b6c3'
down_revision: Union[str, Sequence[str], None] = 'fcf2710fd015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCAL_EMBEDDING_MODEL_ID = "00000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "models",
        sa.Column("modality", sa.String(), server_default="chat", nullable=False),
    )
    op.add_column("models", sa.Column("dimension", sa.Integer(), nullable=True))
    op.add_column("models", sa.Column("collection_name", sa.String(), nullable=True))

    # Seed the built-in local embedding model, preserving the EXACT dimension
    # this deployment's existing "chunks_bge_m3" collection was already
    # created with (RAGHUB_EMBEDDING_DIM at migration time) -- a hardcoded
    # 1024 here would silently mismatch a deployment running a smaller/larger
    # dimension (e.g. this dev environment runs RAGHUB_EMBEDDING_DIM=384) and
    # break every retrieval call once ensure_collection starts validating
    # dimension against the row (Task 3). collection_name is the LITERAL
    # existing constant, not a derived name -- this row must resolve to the
    # collection that already holds every pre-existing indexed document.
    dimension = get_settings().embedding_dim
    op.execute(
        sa.text(
            """
            INSERT INTO models (
                id, created_at, litellm_model_name, display_name, provider_kind,
                enabled, sync_status, tools_unreliable, is_utility,
                supports_reasoning, default_reasoning_effort, supports_vision,
                modality, dimension, collection_name
            ) VALUES (
                :id, now(), 'local-embeddings', 'Local Embeddings (bge-m3)', 'tei',
                true, 'synced', false, false,
                false, 'off', false,
                'embedding', :dimension, 'chunks_bge_m3'
            )
            """
        ).bindparams(
            sa.bindparam("id", value=LOCAL_EMBEDDING_MODEL_ID, type_=sa.Uuid()),
            sa.bindparam("dimension", value=dimension, type_=sa.Integer()),
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text("DELETE FROM models WHERE id = :id").bindparams(
            sa.bindparam("id", value=LOCAL_EMBEDDING_MODEL_ID, type_=sa.Uuid())
        )
    )
    op.drop_column("models", "collection_name")
    op.drop_column("models", "dimension")
    op.drop_column("models", "modality")
