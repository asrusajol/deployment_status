"""add client_version_records and bitbucket_main_branch_status tables

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'bitbucket_main_branch_status',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('version', sa.String(length=100), nullable=True),
        sa.Column('pr_number', sa.Integer(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'client_version_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('environment', sa.String(length=10), nullable=False),
        sa.Column('current_version', sa.String(length=100), nullable=False),
        sa.Column('previous_version', sa.String(length=100), nullable=True),
        sa.Column('main_version', sa.String(length=100), nullable=True),
        sa.Column('main_pr_number', sa.Integer(), nullable=True),
        sa.Column('deployment_request_id', sa.Integer(), nullable=False),
        sa.Column('recorded_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['deployment_request_id'], ['deployment_requests.id']),
        sa.ForeignKeyConstraint(['recorded_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    # Cast the environment column to the deploymentenvironment enum type
    op.execute('ALTER TABLE client_version_records ALTER COLUMN environment TYPE deploymentenvironment USING environment::deploymentenvironment')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('client_version_records')
    op.drop_table('bitbucket_main_branch_status')
