"""Password reset tokens, plus the composite index analytics relies on.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        # SHA-256 of the token; the plain value exists only in the email.
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("requested_ip", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_password_resets_id"), "password_resets", ["id"])
    op.create_index(op.f("ix_password_resets_email"), "password_resets", ["email"])
    op.create_index(op.f("ix_password_resets_token_hash"), "password_resets", ["token_hash"])

    # Every analytics query filters on user_id and orders or ranges on
    # date. A composite index serves both in one pass; the single-column
    # user_id index alone would still require sorting the matched rows.
    op.create_index(
        "ix_activities_user_date",
        "activities",
        ["user_id", "date"],
    )


def downgrade() -> None:
    op.drop_index("ix_activities_user_date", table_name="activities")
    op.drop_table("password_resets")
