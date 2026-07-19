"""metadata fields and document meta

Revision ID: f7955f86d1af
Revises: e2da06a8007a
Create Date: 2026-07-19 14:02:11.302104

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f7955f86d1af'
down_revision: Union[str, Sequence[str], None] = 'e2da06a8007a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Plan H (DOC-6): admin-defined metadata fields per workspace
    (metadata_fields, text|date|select) and the document-level values that
    fill them in (documents.meta, JSONB — mirrored to the Qdrant payload by
    modules/documents/metadata.py + modules/retrieval/service.py).
    """
    op.create_table(
        'metadata_fields',
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('field_type', sa.String(), nullable=False),
        sa.Column('options', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'name', name='uq_metadata_fields_ws_name'),
    )
    op.create_index(
        op.f('ix_metadata_fields_workspace_id'), 'metadata_fields', ['workspace_id'], unique=False
    )
    op.add_column(
        'documents', sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('documents', 'meta')
    op.drop_index(op.f('ix_metadata_fields_workspace_id'), table_name='metadata_fields')
    op.drop_table('metadata_fields')
