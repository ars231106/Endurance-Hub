"""End-to-end smoke test.

1. Start the API:  uvicorn app.main:app --reload
2. In another terminal (same venv):  python smoke_test.py

Registers a throwaway user, reads its OTP straight from the database,
verifies, then exercises every endpoint including error cases.
"""
import json
import random
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
TOKEN = None


def call(method, path, body=None, expect=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as resp:
            status, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode()
    ok = (expect is None) or (status == expect)
    print(f"{'PASS' if ok else 'FAIL'}  {method:6} {path:25} -> {status}")
    if not ok:
        print("      ", text[:200])
    return json.loads(text) if text else None


def fetch_otp(email):
    # Test-only shortcut: read the code from the DB instead of an inbox.
    from app.databases.database import SessionLocal
    from app.models.otp import EmailOTP
    db = SessionLocal()
    try:
        otp = db.query(EmailOTP).filter(EmailOTP.email == email).first()
        return otp.code if otp else None
    finally:
        db.close()


email = f"smoke{random.randint(1000, 9999)}@test.com"

call("GET", "/health", expect=200)
call("POST", "/register", {"name": "Smoke", "email": email, "password": "Secret123!"}, expect=201)
call("POST", "/register", {"name": "Smoke", "email": email, "password": "Secret123!"}, expect=409)

# Unverified accounts can't log in yet.
call("POST", "/login", {"email": email, "password": "Secret123!"}, expect=403)

# OTP flow: wrong code, cooldown, then the real code.
call("POST", "/verify-email", {"email": email, "code": "000000"}, expect=400)
call("POST", "/resend-otp", {"email": email}, expect=429)  # 60s cooldown active
code = fetch_otp(email)
resp = call("POST", "/verify-email", {"email": email, "code": code}, expect=200)
TOKEN = resp["access_token"]

call("GET", "/me", expect=200)
call("POST", "/login", {"email": email, "password": "wrong"}, expect=401)
call("POST", "/login", {"email": email, "password": "Secret123!"}, expect=200)

a1 = call("POST", "/activities", {"activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 6}, expect=201)
a2 = call("POST", "/activities", {"activity_type": "ride", "distance_km": 20, "duration_min": 45, "rpe": 4, "notes": "easy spin"}, expect=201)
call("POST", "/activities", {"activity_type": "run", "distance_km": -1, "duration_min": 30, "rpe": 6}, expect=422)

# Duration-only sports: no distance required, and any sent is discarded.
a3 = call("POST", "/activities", {"activity_type": "strength", "duration_min": 55, "rpe": 8}, expect=201)
assert a3["distance_km"] is None, "strength should not store a distance"
a4 = call("POST", "/activities", {"activity_type": "football", "duration_min": 90, "rpe": 7, "distance_km": 8}, expect=201)
assert a4["distance_km"] is None, "sent distance should be stripped for sports"
call("POST", "/activities", {"activity_type": "run", "duration_min": 30, "rpe": 6}, expect=422)       # run needs distance
call("POST", "/activities", {"activity_type": "quidditch", "duration_min": 30, "rpe": 6}, expect=422)  # unknown type
# New distance sports exist
call("POST", "/activities", {"activity_type": "row", "distance_km": 6, "duration_min": 28, "rpe": 7}, expect=201)
call("POST", "/activities", {"activity_type": "hike", "distance_km": 14, "duration_min": 210, "rpe": 5}, expect=201)

call("GET", "/activities", expect=200)
call("GET", f"/activities/{a1['id']}", expect=200)
call("PUT", f"/activities/{a1['id']}", {"rpe": 8}, expect=200)
call("GET", "/activities/999999", expect=404)

s = call("GET", "/analytics/summary", expect=200)
assert s["other_sessions"] >= 2, "duration-only sessions should be counted"
call("GET", "/analytics/weekly", expect=200)
r = call("GET", "/analytics/records", expect=200)
assert r["longest_distance"]["distance_km"] is not None, "distance record must come from a distance sport"
call("GET", "/analytics/streak", expect=200)
call("GET", "/analytics/by-sport", expect=200)

# Heart-rate profile and HR-tagged sessions.
prof = call("PUT", "/me/profile", {"birth_year": 2004, "resting_hr": 55}, expect=200)
assert prof["birth_year"] == 2004 and prof["resting_hr"] == 55
call("PUT", "/me/profile", {"resting_hr": 400}, expect=422)   # physiologically absurd
hr_act = call("POST", "/activities", {"activity_type": "crossfit", "duration_min": 40, "rpe": 8, "avg_hr": 165}, expect=201)
assert hr_act["avg_hr"] == 165, "avg_hr should round-trip"
call("POST", "/activities", {"activity_type": "run", "distance_km": 5, "duration_min": 25, "rpe": 7, "avg_hr": 300}, expect=422)

lm = call("GET", "/analytics/load-metrics", expect=200)
assert lm["acute_load"] > 0, "this week's load should be non-zero"
assert lm["acwr_status"] in {"building baseline", "detraining", "optimal", "elevated", "spike"}
assert len(lm["daily_load"]) == 28, "daily load series should cover 28 days"

# Every distance sport gets a threshold, defaulted where history is thin.
th = call("GET", "/analytics/thresholds", expect=200)
assert set(th) == {"run", "ride", "swim", "row", "hike", "walk"}, "all distance sports need a threshold"
assert th["hike"]["is_default"] is False, "the 14km hike should personalise the hike threshold"
assert th["swim"]["is_default"] is True, "no swims logged, so swim should fall back to the default"

# Changing a distance sport into a duration-only one clears the distance.
moved = call("PUT", f"/activities/{a1['id']}", {"activity_type": "yoga"}, expect=200)
assert moved["distance_km"] is None, "distance should be cleared when type becomes duration-only"
call("PUT", f"/activities/{a1['id']}", {"activity_type": "run"}, expect=422)  # can't go back without a distance
call("PUT", f"/activities/{a1['id']}", {"activity_type": "run", "distance_km": 5}, expect=200)

call("DELETE", f"/activities/{a1['id']}", expect=204)
call("DELETE", f"/activities/{a2['id']}", expect=204)

# Account deletion: wrong password rejected, then schedule + cancel.
call("POST", "/me/delete", {"password": "wrong"}, expect=401)
call("POST", "/me/cancel-deletion", expect=400)  # nothing scheduled yet
me = call("POST", "/me/delete", {"password": "Secret123!"}, expect=200)
assert me["delete_requested_at"], "deletion timestamp missing"
call("POST", "/me/delete", {"password": "Secret123!"}, expect=400)  # already scheduled
me = call("POST", "/me/cancel-deletion", expect=200)
assert me["delete_requested_at"] is None, "cancellation failed"

print("\nSmoke test finished.")
