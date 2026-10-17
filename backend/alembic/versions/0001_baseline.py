"""Baseline schema: users, activities, email_otps.

This matches the schema that create_all + schema_sync produced before
Alembic existed. An already-deployed database is stamped at this revision
rather than having it re-applied - see app/databases/migrations.py.

Revision ID: 0001
Revises:
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("delete_requested_at", sa.DateTime(), nullable=True),
        sa.Column("birth_year", sa.Integer(), nullable=True),
        sa.Column("resting_hr", sa.Integer(), nullable=True),
        sa.Column("max_hr", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"])

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("activity_type", sa.String(), nullable=False),
        # Nullable: strength work and sports have no meaningful distance.
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("duration_min", sa.Float(), nullable=False),
        sa.Column("rpe", sa.Integer(), nullable=False),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_activities_id"), "activities", ["id"])
    op.create_index(op.f("ix_activities_user_id"), "activities", ["user_id"])
    op.create_index(op.f("ix_activities_date"), "activities", ["date"])

    op.create_table(
        "email_otps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_otps_id"), "email_otps", ["id"])
    op.create_index(op.f("ix_email_otps_email"), "email_otps", ["email"])


def downgrade() -> None:
    op.drop_table("email_otps")
    op.drop_table("activities")
    op.drop_table("users")
