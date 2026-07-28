"""One-time migration: allow activities without a distance.

Strength work, sports and classes are logged with duration and effort
only, so distance_km must accept NULL.

    cd backend
    venv\\Scripts\\activate
    python migrate_activity_types.py
"""
from sqlalchemy import text

from app.databases.database import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE activities ALTER COLUMN distance_km DROP NOT NULL"))

print("activities.distance_km is now nullable.")
print("You can log strength, sports and classes with duration + effort only.")
