"""add_lat_lon_to_driving_events

Revision ID: f8e6b5aa61ed
Revises: 6985ed4f7a05
Create Date: 2026-08-26 13:04:34.565660

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8e6b5aa61ed'
down_revision: Union[str, Sequence[str], None] = '6985ed4f7a05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable Float columns on an already-existing table -- no batch mode
    # needed (same reasoning as ca294b499e12's plain nullable Integer add).
    op.add_column('driving_events', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('driving_events', sa.Column('longitude', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('driving_events', 'longitude')
    op.drop_column('driving_events', 'latitude')
