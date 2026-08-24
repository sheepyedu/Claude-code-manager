"""add_monitor_direct_ssh_principal

Revision ID: b18d4f6a2c90
Revises: a56a13b7f287
Create Date: 2026-08-24 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b18d4f6a2c90"
down_revision: Union[str, None] = "a56a13b7f287"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sub_agent_sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "direct_ssh",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("initiating_user_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("initiating_user_role", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("execution_mode", sa.String(length=20), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_sub_agent_monitor_direct_ssh_principal",
            "(direct_ssh AND agent_type = 'monitor' AND source = 'ccm' AND "
            "initiating_user_role IN ('admin', 'super_admin') AND "
            "execution_mode = 'unrestricted') "
            "OR (NOT direct_ssh AND initiating_user_id IS NULL AND "
            "initiating_user_role IS NULL AND execution_mode IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("sub_agent_sessions", schema=None) as batch_op:
        batch_op.drop_constraint(
            "ck_sub_agent_monitor_direct_ssh_principal",
            type_="check",
        )
        batch_op.drop_column("execution_mode")
        batch_op.drop_column("initiating_user_role")
        batch_op.drop_column("initiating_user_id")
        batch_op.drop_column("direct_ssh")
