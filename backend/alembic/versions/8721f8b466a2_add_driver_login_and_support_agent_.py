"""add driver login credentials, assigned vehicle, and support agent phone number

Revision ID: 8721f8b466a2
Revises: 3d5f81ac9e62
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8721f8b466a2'
down_revision: Union[str, Sequence[str], None] = '3d5f81ac9e62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('drivers') as batch_op:
        batch_op.add_column(sa.Column('password_hash', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('assigned_vehicle_id', sa.Integer(), nullable=True))
        batch_op.create_unique_constraint('uq_drivers_email', ['email'])
        batch_op.create_foreign_key(
            'fk_drivers_assigned_vehicle_id_vehicles',
            'vehicles',
            ['assigned_vehicle_id'],
            ['vehicle_id'],
        )
    with op.batch_alter_table('support_agents') as batch_op:
        batch_op.add_column(sa.Column('phone_number', sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('support_agents') as batch_op:
        batch_op.drop_column('phone_number')
    with op.batch_alter_table('drivers') as batch_op:
        batch_op.drop_constraint('fk_drivers_assigned_vehicle_id_vehicles', type_='foreignkey')
        batch_op.drop_constraint('uq_drivers_email', type_='unique')
        batch_op.drop_column('assigned_vehicle_id')
        batch_op.drop_column('password_hash')
