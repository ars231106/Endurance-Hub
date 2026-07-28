"""One-time migration: heart-rate support.

Adds optional physiology to users (used to estimate max heart rate) and
an average heart rate per activity, so users with a wearable can get an
effort score derived from their data.

    cd backend
    venv\\Scripts\\activate
    python migrate_hr.py
"""
from sqlalchemy import text

from app.databases.database import engine

STATEMENTS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS birth_year INTEGER NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS resting_hr INTEGER NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS max_hr INTEGER NULL",
    "ALTER TABLE activities ADD COLUMN IF NOT EXISTS avg_hr INTEGER NULL",
]

with engine.begin() as conn:
    for sql in STATEMENTS:
        conn.execute(text(sql))

print("Heart-rate columns added:")
print("  users.birth_year, users.resting_hr, users.max_hr")
print("  activities.avg_hr")
