"""add ces_score to chat_sessions

Revision ID: ca294b499e12
Revises: c7fbb78e2f74
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ca294b499e12'
down_revision: Union[str, Sequence[str], None] = 'c7fbb78e2f74'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Task 22: a nullable plain-Integer column on an already-existing table,
    # no FK/enum involved -- unlike 054e88c6af09's FK adds or f047842cb6ff's
    # native-enum column, this needs no batch mode (SQLite's ADD COLUMN
    # supports a bare nullable column directly) and no Postgres-specific
    # CREATE TYPE step.
    op.add_column('chat_sessions', sa.Column('ces_score', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('chat_sessions', 'ces_score')
