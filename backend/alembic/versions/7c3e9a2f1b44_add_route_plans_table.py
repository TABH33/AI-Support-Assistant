"""add_route_plans_table

Revision ID: 7c3e9a2f1b44
Revises: f8e6b5aa61ed
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c3e9a2f1b44'
down_revision: Union[str, Sequence[str], None] = 'f8e6b5aa61ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'route_plans',
        sa.Column('route_plan_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('created_by_role', sa.String(length=32), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('origin_label', sa.String(length=255), nullable=False),
        sa.Column('destination_label', sa.String(length=255), nullable=False),
        sa.Column('distance_km', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('duration_min', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('geometry', sa.JSON(), nullable=True),
        sa.Column('warnings', sa.JSON(), nullable=False),
        sa.Column('unavailable', sa.Boolean(), nullable=False),
        sa.Column('unavailable_reason', sa.String(length=32), nullable=True),
        sa.Column(
            'status',
            sa.Enum('active', 'completed', name='route_plan_status'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id']),
        sa.PrimaryKeyConstraint('route_plan_id'),
    )
    op.create_index(op.f('ix_route_plans_customer_id'), 'route_plans', ['customer_id'])
    op.create_index(op.f('ix_route_plans_created_at'), 'route_plans', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_route_plans_created_at'), table_name='route_plans')
    op.drop_index(op.f('ix_route_plans_customer_id'), table_name='route_plans')
    op.drop_table('route_plans')
    sa.Enum(name='route_plan_status').drop(op.get_bind(), checkfirst=True)
