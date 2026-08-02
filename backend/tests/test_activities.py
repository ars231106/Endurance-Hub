"""Activity CRUD, the sport taxonomy, and per-user data isolation."""
import uuid

from conftest import read_otp


def test_create_and_list_distance_activity(client, verified_user):
    h = verified_user["headers"]
    r = client.post("/activities", json={
        "activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 6,
    }, headers=h)
    assert r.status_code == 201
    assert r.json()["distance_km"] == 5

    listed = client.get("/activities", headers=h).json()
    assert any(a["id"] == r.json()["id"] for a in listed)


def test_distance_sports_require_a_distance(client, verified_user):
    r = client.post("/activities", json={"activity_type": "run", "duration_min": 30, "rpe": 6},
                    headers=verified_user["headers"])
    assert r.status_code == 422


def test_duration_only_sports_store_no_distance(client, verified_user):
    h = verified_user["headers"]
    r = client.post("/activities", json={"activity_type": "strength", "duration_min": 45, "rpe": 8},
                    headers=h)
    assert r.status_code == 201 and r.json()["distance_km"] is None

    # A distance sent for a non-distance sport is stripped, not stored.
    r2 = client.post("/activities", json={
        "activity_type": "football", "duration_min": 90, "rpe": 7, "distance_km": 8,
    }, headers=h)
    assert r2.status_code == 201 and r2.json()["distance_km"] is None


def test_unknown_sport_is_rejected(client, verified_user):
    r = client.post("/activities", json={"activity_type": "quidditch", "duration_min": 30, "rpe": 5},
                    headers=verified_user["headers"])
    assert r.status_code == 422


def test_new_distance_sports_exist(client, verified_user):
    h = verified_user["headers"]
    for sport, km, mins in [("row", 6, 28), ("hike", 14, 210), ("walk", 4, 45), ("swim", 1.5, 35)]:
        r = client.post("/activities", json={
            "activity_type": sport, "distance_km": km, "duration_min": mins, "rpe": 5,
        }, headers=h)
        assert r.status_code == 201, sport


def test_invalid_values_are_rejected(client, verified_user):
    h = verified_user["headers"]
    bad = [
        {"activity_type": "run", "distance_km": -1, "duration_min": 30, "rpe": 6},
        {"activity_type": "run", "distance_km": 5, "duration_min": 0, "rpe": 6},
        {"activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 11},
        {"activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 6, "avg_hr": 300},
    ]
    for payload in bad:
        assert client.post("/activities", json=payload, headers=h).status_code == 422, payload


def test_heart_rate_round_trips(client, verified_user):
    r = client.post("/activities", json={
        "activity_type": "crossfit", "duration_min": 40, "rpe": 8, "avg_hr": 165,
    }, headers=verified_user["headers"])
    assert r.status_code == 201 and r.json()["avg_hr"] == 165


def test_partial_update_leaves_other_fields_alone(client, verified_user):
    h = verified_user["headers"]
    a = client.post("/activities", json={
        "activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 6, "notes": "easy",
    }, headers=h).json()

    updated = client.put(f"/activities/{a['id']}", json={"rpe": 9}, headers=h).json()
    assert updated["rpe"] == 9
    assert updated["distance_km"] == 5 and updated["notes"] == "easy"


def test_switching_to_a_duration_sport_clears_distance(client, verified_user):
    h = verified_user["headers"]
    a = client.post("/activities", json={
        "activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 6,
    }, headers=h).json()

    moved = client.put(f"/activities/{a['id']}", json={"activity_type": "yoga"}, headers=h)
    assert moved.status_code == 200 and moved.json()["distance_km"] is None

    # Going back needs a distance again.
    assert client.put(f"/activities/{a['id']}", json={"activity_type": "run"},
                      headers=h).status_code == 422
    assert client.put(f"/activities/{a['id']}", json={"activity_type": "run", "distance_km": 5},
                      headers=h).status_code == 200


def test_delete(client, verified_user):
    h = verified_user["headers"]
    a = client.post("/activities", json={
        "activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 6,
    }, headers=h).json()
    assert client.delete(f"/activities/{a['id']}", headers=h).status_code == 204
    assert client.get(f"/activities/{a['id']}", headers=h).status_code == 404


def test_missing_activity_returns_404(client, verified_user):
    assert client.get("/activities/999999", headers=verified_user["headers"]).status_code == 404


def test_users_cannot_see_or_touch_each_others_activities(client, verified_user):
    """The security-critical one: ownership is enforced on every query."""
    owner_h = verified_user["headers"]
    mine = client.post("/activities", json={
        "activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 6,
    }, headers=owner_h).json()

    # A second, unrelated account. Its email is generated here rather than
    # taken from the unique_email fixture, which verified_user has already
    # consumed - both would otherwise receive the same address.
    other_email = f"other_{uuid.uuid4().hex[:10]}@example.test"
    client.post("/register", json={"name": "B", "email": other_email, "password": "Secret123!"})
    token = client.post("/verify-email",
                        json={"email": other_email, "code": read_otp(other_email)}).json()["access_token"]
    other_h = {"Authorization": f"Bearer {token}"}

    # Not visible in their list, and indistinguishable from missing.
    assert all(a["id"] != mine["id"] for a in client.get("/activities", headers=other_h).json())
    assert client.get(f"/activities/{mine['id']}", headers=other_h).status_code == 404
    assert client.put(f"/activities/{mine['id']}", json={"rpe": 1}, headers=other_h).status_code == 404
    assert client.delete(f"/activities/{mine['id']}", headers=other_h).status_code == 404

    # And it's still there, untouched, for its owner.
    assert client.get(f"/activities/{mine['id']}", headers=owner_h).json()["rpe"] == 6
