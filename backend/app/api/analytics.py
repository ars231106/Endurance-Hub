import statistics
from datetime import date, timedelta
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.constants import (
    DEFAULT_THRESHOLD_PACE,
    DISTANCE_TYPES,
    RIEGEL_EXPONENT,
    RIEGEL_MAX_FACTOR,
    THRESHOLD_LOOKBACK_DAYS,
    THRESHOLD_MIN_KM,
    THRESHOLD_MIN_MINUTES,
    THRESHOLD_SHORT_MIN_MINUTES,
    THRESHOLD_TARGET_MINUTES,
)
from app.databases.database import get_db
from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.user import User

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _user_activities(db: Session, user: User) -> List[Activity]:
    return db.query(Activity).filter(Activity.user_id == user.id).all()


def _with_distance(acts: List[Activity]) -> List[Activity]:
    # Strength work and sports carry no distance, so distance-based
    # metrics (pace, mileage, longest run) must ignore them.
    return [a for a in acts if a.distance_km]


@router.get("/summary")
def summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acts = _user_activities(db, current_user)
    if not acts:
        return {
            "total_activities": 0,
            "total_distance_km": 0,
            "total_duration_min": 0,
            "avg_pace_min_per_km": None,
            "avg_rpe": None,
            "longest_activity_km": 0,
            "distance_sessions": 0,
            "other_sessions": 0,
        }

    dist_acts = _with_distance(acts)
    total_km = sum(a.distance_km for a in dist_acts)
    total_min = sum(a.duration_min for a in acts)
    # Pace uses only the time spent covering distance, otherwise a gym
    # session would drag the average pace down.
    dist_min = sum(a.duration_min for a in dist_acts)

    return {
        "total_activities": len(acts),
        "total_distance_km": round(total_km, 2),
        "total_duration_min": round(total_min, 1),
        "avg_pace_min_per_km": round(dist_min / total_km, 2) if total_km else None,
        "avg_rpe": round(sum(a.rpe for a in acts) / len(acts), 1),
        "longest_activity_km": round(max((a.distance_km for a in dist_acts), default=0), 2),
        "distance_sessions": len(dist_acts),
        "other_sessions": len(acts) - len(dist_acts),
    }


@router.get("/weekly")
def weekly(
    weeks: int = 8,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acts = _user_activities(db, current_user)
    weeks = max(1, min(weeks, 52))

    # Anchor every bucket to a Monday so weeks line up with the calendar.
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())

    buckets = []
    for i in range(weeks - 1, -1, -1):
        week_start = this_monday - timedelta(weeks=i)
        week_end = week_start + timedelta(days=6)
        in_week = [a for a in acts if week_start <= a.date <= week_end]

        # Training load = duration x RPE summed (the "session-RPE" method
        # used in sports science). It works for every sport, which is why
        # it - not distance - is the headline consistency metric.
        buckets.append({
            "week_start": week_start.isoformat(),
            "activities": len(in_week),
            "distance_km": round(sum(a.distance_km or 0 for a in in_week), 2),
            "duration_min": round(sum(a.duration_min for a in in_week), 1),
            "training_load": round(sum(a.duration_min * a.rpe for a in in_week)),
        })
    return buckets


@router.get("/records")
def records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    acts = _user_activities(db, current_user)
    if not acts:
        return {"longest_distance": None, "longest_duration": None, "best_pace": None}

    def as_out(a: Activity):
        return {
            "id": a.id,
            "date": a.date.isoformat(),
            "activity_type": a.activity_type,
            "distance_km": a.distance_km,
            "duration_min": a.duration_min,
            "pace_min_per_km": round(a.duration_min / a.distance_km, 2) if a.distance_km else None,
        }

    dist_acts = _with_distance(acts)
    # Best pace only counts activities of at least 1 km, so a 50 m sprint
    # can't claim the record.
    paceable = [a for a in dist_acts if a.distance_km >= 1]

    return {
        "longest_distance": as_out(max(dist_acts, key=lambda a: a.distance_km)) if dist_acts else None,
        # Longest duration spans every sport - a three-hour football match counts.
        "longest_duration": as_out(max(acts, key=lambda a: a.duration_min)),
        "best_pace": as_out(min(paceable, key=lambda a: a.duration_min / a.distance_km)) if paceable else None,
    }


@router.get("/streak")
def streak(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # A streak = consecutive calendar days with at least one activity.
    active_days = sorted({a.date for a in _user_activities(db, current_user)})
    if not active_days:
        return {"current_streak": 0, "longest_streak": 0}

    # Longest streak: walk the sorted days and count consecutive runs.
    longest = run = 1
    for prev, curr in zip(active_days, active_days[1:]):
        run = run + 1 if (curr - prev).days == 1 else 1
        longest = max(longest, run)

    # Current streak: count back from today (or yesterday, so the streak
    # isn't "broken" before today's workout is logged).
    today = date.today()
    day_set = set(active_days)
    anchor = today if today in day_set else today - timedelta(days=1)
    current = 0
    while anchor in day_set:
        current += 1
        anchor -= timedelta(days=1)

    return {"current_streak": current, "longest_streak": longest}


@router.get("/load-metrics")
def load_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Derived training-status metrics used by coaches, computed from the
    session-RPE load already stored on every activity - no sensors needed.

    ACWR  : acute (7d) load vs chronic (28d weekly average). Ratios above
            ~1.5 mark the spikes associated with injury risk; below 0.8
            suggests detraining.
    Monotony (Foster): mean daily load / standard deviation of daily load
            across the week. Above ~2.0 means every day looks the same,
            which predicts staleness - good weeks have genuinely hard and
            genuinely easy days.
    Strain: weekly load x monotony. High volume AND high sameness is the
            combination that precedes breakdown.
    """
    acts = _user_activities(db, current_user)
    today = date.today()

    # One load number per calendar day.
    daily = {}
    for a in acts:
        daily[a.date] = daily.get(a.date, 0) + a.duration_min * a.rpe

    def window(days: int) -> List[float]:
        return [daily.get(today - timedelta(days=i), 0.0) for i in range(days)]

    last7 = window(7)
    last28 = window(28)
    acute = sum(last7)
    chronic_weekly = sum(last28) / 4  # 28 days expressed as a weekly average

    # Rest days are real training data, so they count as zeros - but a user
    # with almost no history would get meaningless ratios.
    history_days = (today - min(daily)).days + 1 if daily else 0
    enough_history = history_days >= 14 and chronic_weekly > 0

    acwr = round(acute / chronic_weekly, 2) if enough_history else None
    if acwr is None:
        acwr_status = "building baseline"
    elif acwr < 0.8:
        acwr_status = "detraining"
    elif acwr <= 1.3:
        acwr_status = "optimal"
    elif acwr <= 1.5:
        acwr_status = "elevated"
    else:
        acwr_status = "spike"

    # Population standard deviation: these seven days are the whole week,
    # not a sample drawn from it.
    sd = statistics.pstdev(last7)
    mean = sum(last7) / 7
    training_days = sum(1 for v in last7 if v > 0)

    if mean == 0:
        monotony, monotony_status = None, "no training logged this week"
    elif training_days < 3:
        # Foster's ratio assumes a real training week; with one or two
        # sessions the rest days dominate the SD and it reads falsely low.
        monotony, monotony_status = None, "needs 3+ training days"
    elif sd == 0:
        # Identical load every single day is maximum monotony, not an
        # absence of it - the ratio is infinite, so report it capped.
        monotony, monotony_status = 5.0, "too uniform"
    else:
        monotony = round(mean / sd, 2)
        monotony_status = (
            "good variation" if monotony < 1.5
            else "moderate" if monotony < 2.0
            else "too uniform"
        )

    strain = round(acute * monotony) if monotony else None

    return {
        "acute_load": round(acute),
        "chronic_load": round(chronic_weekly),
        "acwr": acwr,
        "acwr_status": acwr_status,
        "monotony": monotony,
        "monotony_status": monotony_status,
        "strain": strain,
        "history_days": history_days,
        # Oldest first, so the frontend can draw it straight as a sparkline.
        "daily_load": [round(v) for v in reversed(last28)],
    }


def _riegel_factor(minutes: float) -> float:
    """How much slower than a short effort's pace the ~60 minute threshold
    pace is likely to be. You can hold a faster pace for 10 minutes than
    for an hour, so a short session overstates fitness unless corrected.
    Capped so a very short sprint can't produce a wild number."""
    factor = (THRESHOLD_TARGET_MINUTES / minutes) ** RIEGEL_EXPONENT
    return min(factor, RIEGEL_MAX_FACTOR)


@router.get("/thresholds")
def thresholds(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Estimated threshold pace for every distance sport.

    Threshold pace is roughly the effort you could hold for an hour, and
    it's the yardstick the RPE suggestion measures a session against.

    Three tiers, best first:
      personal  - fastest session past this sport's distance/time minimum
      estimated - fastest shorter effort, corrected via Riegel
      default   - a generic value, when there's no history at all

    Only the last THRESHOLD_LOOKBACK_DAYS count, so the number tracks
    current fitness instead of a personal best that may be long gone.
    Every response says which tier it came from and what it was based on,
    so the UI can be honest about how much to trust it.
    """
    cutoff = date.today() - timedelta(days=THRESHOLD_LOOKBACK_DAYS)

    best_full, best_short, counts = {}, {}, {}

    for a in _user_activities(db, current_user):
        if a.activity_type not in DISTANCE_TYPES or not a.distance_km:
            continue
        if a.date < cutoff:
            continue

        pace = a.duration_min / a.distance_km
        min_km = THRESHOLD_MIN_KM.get(a.activity_type, 3.0)

        if a.duration_min >= THRESHOLD_MIN_MINUTES and a.distance_km >= min_km:
            counts[a.activity_type] = counts.get(a.activity_type, 0) + 1
            current = best_full.get(a.activity_type)
            if current is None or pace < current[0]:
                best_full[a.activity_type] = (pace, a)

        elif a.duration_min >= THRESHOLD_SHORT_MIN_MINUTES:
            # Too short to count directly, but still evidence. Adjust it
            # to what the same athlete could likely hold for an hour.
            adjusted = pace * _riegel_factor(a.duration_min)
            current = best_short.get(a.activity_type)
            if current is None or adjusted < current[0]:
                best_short[a.activity_type] = (adjusted, a, pace)

    def based_on(a, raw_pace=None):
        info = {
            "activity_id": a.id,
            "date": a.date.isoformat(),
            "distance_km": a.distance_km,
            "duration_min": a.duration_min,
        }
        if raw_pace is not None:
            info["raw_pace_min_per_km"] = round(raw_pace, 2)
        return info

    out = {}
    for sport in DISTANCE_TYPES:
        if sport in best_full:
            pace, a = best_full[sport]
            entry = {
                "threshold_pace_min_per_km": round(pace, 2),
                "source": "personal",
                "qualifying_sessions": counts.get(sport, 0),
                "based_on": based_on(a),
            }
        elif sport in best_short:
            adjusted, a, raw = best_short[sport]
            entry = {
                "threshold_pace_min_per_km": round(adjusted, 2),
                "source": "estimated",
                "qualifying_sessions": 0,
                "based_on": based_on(a, raw),
                "needs_km": THRESHOLD_MIN_KM.get(sport, 3.0),
                "needs_minutes": THRESHOLD_MIN_MINUTES,
            }
        else:
            entry = {
                "threshold_pace_min_per_km": DEFAULT_THRESHOLD_PACE[sport],
                "source": "default",
                "qualifying_sessions": 0,
                "based_on": None,
                "needs_km": THRESHOLD_MIN_KM.get(sport, 3.0),
                "needs_minutes": THRESHOLD_MIN_MINUTES,
            }

        # Kept so existing clients don't break when "source" replaces it.
        entry["is_default"] = entry["source"] == "default"
        entry["lookback_days"] = THRESHOLD_LOOKBACK_DAYS
        out[sport] = entry

    return out


@router.get("/by-sport")
def by_sport(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Totals grouped by sport - powers the per-sport goal tracking."""
    totals = {}
    for a in _user_activities(db, current_user):
        t = totals.setdefault(
            a.activity_type,
            {"activities": 0, "distance_km": 0.0, "duration_min": 0.0, "training_load": 0.0},
        )
        t["activities"] += 1
        t["distance_km"] += a.distance_km or 0
        t["duration_min"] += a.duration_min
        t["training_load"] += a.duration_min * a.rpe

    for t in totals.values():
        t["distance_km"] = round(t["distance_km"], 2)
        t["duration_min"] = round(t["duration_min"], 1)
        t["training_load"] = round(t["training_load"])
    return totals
