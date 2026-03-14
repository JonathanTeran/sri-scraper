"""add knowledge base tables

Revision ID: c7e2a1f3d9b4
Revises: b56d8078b1a8
Create Date: 2026-03-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c7e2a1f3d9b4'
down_revision: Union[str, None] = 'b56d8078b1a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sri_knowledge_base',
        sa.Column('id', sa.Uuid(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('key', sa.String(200), nullable=False),
        sa.Column('successes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failures', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('blocks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_duration_sec', sa.Float(), nullable=False, server_default='0'),
        sa.Column('duration_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=True),
        sa.Column('last_success_at', sa.DateTime(), nullable=True),
        sa.Column('last_failure_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_kb_category_key', 'sri_knowledge_base', ['category', 'key'], unique=True)
    op.create_index(op.f('ix_sri_knowledge_base_category'), 'sri_knowledge_base', ['category'])
    op.create_index(op.f('ix_sri_knowledge_base_key'), 'sri_knowledge_base', ['key'])

    op.create_table(
        'sri_block_events',
        sa.Column('id', sa.Uuid(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('engine', sa.String(50), nullable=False),
        sa.Column('error_type', sa.String(100), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('captcha_variant', sa.String(100), nullable=True),
        sa.Column('captcha_provider', sa.String(50), nullable=True),
        sa.Column('hour_of_day', sa.Integer(), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('context_json', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_block_events_time', 'sri_block_events', ['created_at'])
    op.create_index('ix_block_events_hour', 'sri_block_events', ['hour_of_day'])
    op.create_index(op.f('ix_sri_block_events_engine'), 'sri_block_events', ['engine'])
    op.create_index(op.f('ix_sri_block_events_error_type'), 'sri_block_events', ['error_type'])


def downgrade() -> None:
    op.drop_table('sri_block_events')
    op.drop_table('sri_knowledge_base')
