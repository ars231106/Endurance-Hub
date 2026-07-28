"""One-time migration: adds the delete_requested_at column to users.

    cd backend
    venv\\Scripts\\activate
    python migrate_deletion.py
"""
from sqlalchemy import text

from app.databases.database import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS delete_requested_at TIMESTAMP NULL"
    ))

print("users.delete_requested_at added. Account deletion flow is ready.")
