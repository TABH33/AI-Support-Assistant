"""add_driver_id_to_route_plans

Revision ID: 3d5f81ac9e62
Revises: 7c3e9a2f1b44
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d5f81ac9e62'
down_revision: Union[str, Sequence[str], None] = '7c3e9a2f1b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Batch mode, same reason as 054e88c6af09: SQLite (the dev/test fallback
    # used when a live Postgres+pgvector instance isn't available) has no
    # ALTER TABLE ADD CONSTRAINT support, so adding a FK to an
    # already-existing table requires Alembic's copy-and-move batch
    # strategy. Batch mode is a passthrough on Postgres (`recreate="auto"`
    # only recreates on SQLite), so this runs unchanged there.
    with op.batch_alter_table('route_plans') as batch_op:
        batch_op.add_column(sa.Column('driver_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_route_plans_driver_id_drivers', 'drivers', ['driver_id'], ['driver_id']
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('route_plans') as batch_op:
        batch_op.drop_constraint('fk_route_plans_driver_id_drivers', type_='foreignkey')
        batch_op.drop_column('driver_id')
