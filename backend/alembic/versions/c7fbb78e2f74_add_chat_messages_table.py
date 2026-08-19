"""add chat_messages table

Revision ID: c7fbb78e2f74
Revises: 054e88c6af09
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7fbb78e2f74'
down_revision: Union[str, Sequence[str], None] = '054e88c6af09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Brand-new table (Task 15's ChatMessage model) -- unlike f047842cb6ff's
    # battery_status column (added to an *existing* table via a bare
    # op.add_column, which needed an explicit CREATE TYPE step first on
    # Postgres), op.create_table's own DDL sequence creates the native enum
    # type as part of creating the table, so no separate handling is needed
    # here, matching 482fedba36a5's original chat_sessions/session_status
    # table creation.
    op.create_table(
        'chat_messages',
        sa.Column('chat_message_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('chat_session_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.Enum('user', 'assistant', name='chat_message_role'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('feedback', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['chat_session_id'], ['chat_sessions.chat_session_id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('chat_message_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('chat_messages')
    # Drop the native enum type only on Postgres (symmetric with the implicit
    # creation as part of op.create_table above -- SQLite never had a
    # standalone type to drop; mirrors f047842cb6ff's explicit-drop pattern
    # for battery_status).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects import postgresql

        postgresql.ENUM(name="chat_message_role").drop(bind, checkfirst=True)
