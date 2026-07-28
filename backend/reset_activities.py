"""One-time migration helper.

The activities table gained new columns (date, notes). create_all() cannot
alter existing tables, so run this ONCE to drop the old (empty) table:

    cd backend
    venv\\Scripts\\activate
    python reset_activities.py

Then start the API normally - it will recreate the table with the new schema.
"""
from sqlalchemy import text

from app.databases.database import engine

with engine.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS activities"))

print("Old 'activities' table dropped. Start the API to recreate it with the new schema.")
