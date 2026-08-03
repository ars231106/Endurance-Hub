"""Analytics: summary, weekly buckets, records, streaks, load and thresholds."""
from datetime import date, timedelta

import pytest


@pytest.fixture
def athlete(client, verified_user):
    """A user with a mixed week: two runs, a gym session and a match."""
    h = verified_user["headers"]
    today = date.today()
    sessions = [
        {"activity_type": "run", "distance_km": 10, "duration_min": 55, "rpe": 6,
         "date": str(today - timedelta(days=2))},
        {"activity_type": "run", "distance_km": 5, "duration_min": 30, "rpe": 4,
         "date": str(today - timedelta(days=1))},
        {"activity_type": "strength", "duration_min": 45, "rpe": 8, "date": str(today)},
        {"activity_type": "football", "duration_min": 90, "rpe": 7, "date": str(today)},
    ]
    for s in sessions:
        assert client.post("/activities", json=s, headers=h).status_code == 201
    return h


def test_summary_separates_distance_from_other_sports(client, athlete):
    s = client.get("/analytics/summary", headers=athlete).json()
    assert s["total_activities"] == 4
    assert s["distance_sessions"] == 2 and s["other_sessions"] == 2
    assert s["total_distance_km"] == 15
    # Pace must divide only the time spent covering distance (85 min),
    # not all 220 minutes - otherwise the gym session ruins it.
    assert s["avg_pace_min_per_km"] == pytest.approx(85 / 15, rel=1e-3)


def test_summary_is_empty_for_a_new_user(client, verified_user):
    s = client.get("/analytics/summary", headers=verified_user["headers"]).json()
    assert s["total_activities"] == 0 and s["avg_pace_min_per_km"] is None


def test_weekly_buckets(client, athlete):
    weeks = client.get("/analytics/weekly?weeks=4", headers=athlete).json()
    assert len(weeks) == 4
    # Buckets are Monday-anchored and returned oldest first.
    assert all(date.fromisoformat(w["week_start"]).weekday() == 0 for w in weeks)
    assert weeks[0]["week_start"] < weeks[-1]["week_start"]
    # Load counts every sport, so it exceeds anything distance-only.
    assert sum(w["training_load"] for w in weeks) > 0


def test_weekly_range_is_clamped(client, athlete):
    assert len(client.get("/analytics/weekly?weeks=999", headers=athlete).json()) == 52
    assert len(client.get("/analytics/weekly?weeks=0", headers=athlete).json()) == 1


def test_records_ignore_sports_without_distance(client, athlete):
    r = client.get("/analytics/records", headers=athlete).json()
    assert r["longest_distance"]["distance_km"] == 10
    assert r["best_pace"]["pace_min_per_km"] is not None
    # Longest session spans every sport, so the 90 minute match wins.
    assert r["longest_duration"]["duration_min"] == 90
    assert r["longest_duration"]["activity_type"] == "football"


def test_streak(client, athlete):
    s = client.get("/analytics/streak", headers=athlete).json()
    assert s["current_streak"] == 3 and s["longest_streak"] == 3


def test_load_metrics_shape(client, athlete):
    m = client.get("/analytics/load-metrics", headers=athlete).json()
    assert len(m["daily_load"]) == 28
    assert m["acute_load"] > 0
    assert m["acwr_status"] in {"building baseline", "detraining", "optimal", "elevated", "spike"}
    # Only a few training days this week, so monotony is withheld rather
    # than reported from a meaningless spread.
    assert m["monotony"] is None or m["monotony"] > 0


def test_every_distance_sport_gets_a_threshold(client, athlete):
    t = client.get("/analytics/thresholds", headers=athlete).json()
    assert set(t) == {"run", "ride", "swim", "row", "hike", "walk"}
    # The 10 km run qualifies, so running is personalised...
    assert t["run"]["source"] == "personal"
    assert t["run"]["is_default"] is False        # kept for older clients
    assert t["run"]["qualifying_sessions"] >= 1
    assert t["run"]["based_on"]["distance_km"] == 10
    # ...while untouched sports fall back to a labelled default.
    assert t["swim"]["source"] == "default"
    assert t["swim"]["based_on"] is None
    assert t["swim"]["threshold_pace_min_per_km"] > 0


def test_short_efforts_give_an_estimated_threshold(client, verified_user):
    """A beginner whose longest run is 2 km should still get a personal
    number, adjusted to be slower than their short-effort pace."""
    h = verified_user["headers"]
    client.post("/activities", json={
        "activity_type": "run", "distance_km": 2, "duration_min": 14, "rpe": 7,
    }, headers=h)

    t = client.get("/analytics/thresholds", headers=h).json()["run"]
    assert t["source"] == "estimated"
    assert t["based_on"]["distance_km"] == 2
    # 2 km in 14 min is 7:00/km; the hour estimate must be slower than that.
    assert t["threshold_pace_min_per_km"] > 7.0
    # ...but not absurdly so - the Riegel correction is capped at 15%.
    assert t["threshold_pace_min_per_km"] < 7.0 * 1.15 + 0.01


def test_old_sessions_are_ignored(client, verified_user):
    """A personal best from a year ago must not keep the threshold
    optimistic forever - only the recent window counts."""
    h = verified_user["headers"]
    long_ago = (date.today() - timedelta(days=200)).isoformat()
    client.post("/activities", json={
        "activity_type": "run", "distance_km": 10, "duration_min": 40,
        "rpe": 9, "date": long_ago,
    }, headers=h)

    t = client.get("/analytics/thresholds", headers=h).json()["run"]
    assert t["source"] == "default", "a 200-day-old session should not count"
    assert t["lookback_days"] == 90


def test_by_sport_totals(client, athlete):
    by = client.get("/analytics/by-sport", headers=athlete).json()
    assert by["run"]["activities"] == 2 and by["run"]["distance_km"] == 15
    assert by["strength"]["distance_km"] == 0
    assert by["football"]["training_load"] == 90 * 7


def test_analytics_require_authentication(client):
    for path in ["/analytics/summary", "/analytics/weekly", "/analytics/records",
                 "/analytics/streak", "/analytics/load-metrics", "/analytics/thresholds",
                 "/analytics/by-sport"]:
        assert client.get(path).status_code == 401, path
