"""Email: verification timestamp on users.

Accounts that exist before this migration were created by an
administrator, through an invitation or by the first-run setup, so
they are marked as verified.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14 15:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "email_verified_at", sa.DateTime(timezone=True), nullable=True
            )
        )
    op.execute("UPDATE users SET email_verified_at = created_at")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("email_verified_at")
