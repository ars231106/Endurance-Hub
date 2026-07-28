"""One-time migration: adds the is_verified column to the users table.

Existing accounts are marked verified (they were created before OTP
existed); new registrations must verify via OTP.

    cd backend
    venv\\Scripts\\activate
    python migrate_verification.py
"""
from sqlalchemy import text

from app.databases.database import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    conn.execute(text("UPDATE users SET is_verified = TRUE"))

print("users.is_verified added; existing accounts grandfathered as verified.")
print("Start the API to create the email_otps table.")
