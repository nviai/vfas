"""Dependants of employees (appendix 05-3/BK-QTT-TNCN).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15 00:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "employee_dependents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("tax_code", sa.String(length=14), nullable=False),
        sa.Column("id_type", sa.String(length=4), nullable=False),
        sa.Column("id_number", sa.String(length=30), nullable=False),
        sa.Column("relationship", sa.String(length=2), nullable=False),
        sa.Column("from_month", sa.Date(), nullable=False),
        sa.Column("to_month", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_employee_dependents_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_employee_dependents_employee_id_employees"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_dependents")),
    )
    with op.batch_alter_table("employee_dependents", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_employee_dependents_company_id"),
            ["company_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_employee_dependents_employee_id"),
            ["employee_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("employee_dependents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_employee_dependents_employee_id"))
        batch_op.drop_index(batch_op.f("ix_employee_dependents_company_id"))
    op.drop_table("employee_dependents")
