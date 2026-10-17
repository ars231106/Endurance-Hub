"""Training analytics.

Every endpoint here used to load a user's entire activity history into
Python and loop over it. That is O(all history) per request, and the
dashboard calls seven of these on load - so opening it read the full table
seven times.

The rewrite pushes the work into SQL. Aggregation happens inside Postgres
and one row comes back, so a user with ten years of data costs the same as
one with ten sessions. Where per-row logic genuinely cannot be expressed in
portable SQL (the threshold tiers), the query is narrowed by date and sport
first, so only a bounded slice is ever materialised.

Responses are cached per user for a short TTL and invalidated on write, so
the common case - a dashboard refreshed repeatedly - costs nothing.
"""

import statistics
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.config import settings
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
from app.services import cache

router = APIRouter(prefix="/analytics", tags=["analytics"])


# A session only counts as "distance based" when it actually recorded
# distance. Mirrors the old `if a.distance_km` truthiness check, which
# excluded both NULL and 0.
HAS_DISTANCE = and_(Activity.distance_km.isnot(None), Activity.distance_km > 0)


def _cached(user: User, name: str, compute: Callable[[], Any], **params) -> Any:
    """Serves a computed response from Redis when it is still fresh.

    Keyed by user so one athlete's data can never be served to another -
    the single most dangerous bug a cache can introduce.
    """
    if settings.analytics_cache_seconds <= 0:
        return compute()

    suffix = ":".join(f"{k}={v}" for k, v in sorted(params.items()))
    key = f"analytics:{user.id}:{name}" + (f":{suffix}" if suffix else "")

    hit = cache.cache_get(key)
    if hit is not None:
        return hit

    value = compute()
    cache.cache_set(key, value, settings.analytics_cache_seconds)
    return value


def invalidate_user_cache(user_id: int) -> None:
    """Called after any write to a user's activities."""
    cache.cache_delete_prefix(f"analytics:{user_id}:")


def _base(db: Session, user: User):
    return db.query(Activity).filter(Activity.user_id == user.id)


# --- summary --------------------------------------------------------------

@router.get("/summary")
def summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    def compute():
        # One query, one row back. Conditional sums let the distance-only
        # and all-sports figures be computed in a single pass rather than
        # two queries or two Python loops.
        distance_km = case((HAS_DISTANCE, Activity.distance_km), else_=0.0)
        distance_min = case((HAS_DISTANCE, Activity.duration_min), else_=0.0)
        is_distance = case((HAS_DISTANCE, 1), else_=0)

        row = (
            db.query(
                func.count(Activity.id),
                func.sum(Activity.duration_min),
                func.sum(Activity.rpe),
                func.sum(distance_km),
                func.sum(distance_min),
                func.max(case((HAS_DISTANCE, Activity.distance_km), else_=None)),
                func.sum(is_distance),
            )
            .filter(Activity.user_id == current_user.id)
            .one()
        )

        total, total_min, total_rpe, total_km, dist_min, longest_km, dist_sessions = row
        total = total or 0
        if not total:
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

        total_km = float(total_km or 0)
        dist_min = float(dist_min or 0)
        dist_sessions = int(dist_sessions or 0)

        return {
            "total_activities": total,
            "total_distance_km": round(total_km, 2),
            "total_duration_min": round(float(total_min or 0), 1),
            # Pace divides only the time spent covering distance, otherwise
            # a gym session would drag the average pace down.
            "avg_pace_min_per_km": round(dist_min / total_km, 2) if total_km else None,
            "avg_rpe": round(float(total_rpe or 0) / total, 1),
            "longest_activity_km": round(float(longest_km or 0), 2),
            "distance_sessions": dist_sessions,
            "other_sessions": total - dist_sessions,
        }

    return _cached(current_user, "summary", compute)


# --- weekly ---------------------------------------------------------------

@router.get("/weekly")
def weekly(
    weeks: int = 8,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    weeks = max(1, min(weeks, 52))

    def compute():
        # Anchor every bucket to a Monday so weeks line up with the calendar.
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        window_start = this_monday - timedelta(weeks=weeks - 1)

        # Group by day in SQL, then fold days into weeks in Python. Date
        # truncation to a week is dialect-specific (date_trunc on Postgres,
        # strftime on SQLite); grouping by plain date is portable and still
        # returns at most a few hundred rows.
        rows = (
            db.query(
                Activity.date,
                func.count(Activity.id),
                func.sum(case((HAS_DISTANCE, Activity.distance_km), else_=0.0)),
                func.sum(Activity.duration_min),
                func.sum(Activity.duration_min * Activity.rpe),
            )
            .filter(Activity.user_id == current_user.id, Activity.date >= window_start)
            .group_by(Activity.date)
            .all()
        )

        by_day = {
            r[0]: {
                "activities": r[1] or 0,
                "distance_km": float(r[2] or 0),
                "duration_min": float(r[3] or 0),
                # Training load = duration x RPE (the "session-RPE" method
                # from sports science). It works for every sport, which is
                # why it - not distance - is the headline metric.
                "training_load": float(r[4] or 0),
            }
            for r in rows
        }

        buckets = []
        for i in range(weeks - 1, -1, -1):
            week_start = this_monday - timedelta(weeks=i)
            days = [week_start + timedelta(days=d) for d in range(7)]
            found = [by_day[d] for d in days if d in by_day]
            buckets.append({
                "week_start": week_start.isoformat(),
                "activities": sum(f["activities"] for f in found),
                "distance_km": round(sum(f["distance_km"] for f in found), 2),
                "duration_min": round(sum(f["duration_min"] for f in found), 1),
                "training_load": round(sum(f["training_load"] for f in found)),
            })
        return buckets

    return _cached(current_user, "weekly", compute, weeks=weeks)


# --- records --------------------------------------------------------------

def _as_record(a: Optional[Activity]) -> Optional[dict]:
    if a is None:
        return None
    return {
        "id": a.id,
        "date": a.date.isoformat(),
        "activity_type": a.activity_type,
        "distance_km": a.distance_km,
        "duration_min": a.duration_min,
        "pace_min_per_km": round(a.duration_min / a.distance_km, 2) if a.distance_km else None,
    }


@router.get("/records")
def records(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    def compute():
        # Three ORDER BY ... LIMIT 1 queries. Each returns a single row and
        # rides the (user_id, date) index, instead of sorting the whole
        # history in Python.
        base = _base(db, current_user)

        longest_distance = (
            base.filter(HAS_DISTANCE).order_by(Activity.distance_km.desc()).first()
        )
        # Longest session spans every sport - a three-hour match counts.
        longest_duration = base.order_by(Activity.duration_min.desc()).first()
        # Best pace only counts efforts of at least 1 km, so a 50 m sprint
        # cannot claim the record.
        best_pace = (
            base.filter(HAS_DISTANCE, Activity.distance_km >= 1)
            .order_by((Activity.duration_min / Activity.distance_km).asc())
            .first()
        )

        return {
            "longest_distance": _as_record(longest_distance),
            "longest_duration": _as_record(longest_duration),
            "best_pace": _as_record(best_pace),
        }

    return _cached(current_user, "records", compute)


# --- streak ---------------------------------------------------------------

@router.get("/streak")
def streak(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    def compute():
        # Only DISTINCT dates are needed, never the activities themselves:
        # ten sessions on one day are one streak day. This is at most a few
        # thousand rows even for a decade of daily training.
        rows = (
            db.query(Activity.date)
            .filter(Activity.user_id == current_user.id)
            .distinct()
            .order_by(Activity.date)
            .all()
        )
        active_days = [r[0] for r in rows]
        if not active_days:
            return {"current_streak": 0, "longest_streak": 0}

        longest = run = 1
        for prev, curr in zip(active_days, active_days[1:]):
            run = run + 1 if (curr - prev).days == 1 else 1
            longest = max(longest, run)

        # Count back from today - or yesterday, so the streak isn't reported
        # broken at 9am before today's session is logged.
        today = date.today()
        day_set = set(active_days)
        anchor = today if today in day_set else today - timedelta(days=1)
        current = 0
        while anchor in day_set:
            current += 1
            anchor -= timedelta(days=1)

        return {"current_streak": current, "longest_streak": longest}

    return _cached(current_user, "streak", compute)


# --- load metrics ---------------------------------------------------------

@router.get("/load-metrics")
def load_metrics(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Training-status metrics derived from session-RPE load. No sensors needed.

    ACWR     : acute (7d) load vs chronic (28d weekly average). Above ~1.5
               marks the spikes associated with injury risk; below 0.8
               suggests detraining.
    Monotony : mean daily load / standard deviation across the week. Above
               ~2.0 means every day looks the same, which predicts
               staleness - good weeks have genuinely hard and easy days.
    Strain   : weekly load x monotony. High volume AND high sameness is the
               combination that precedes breakdown.
    """
    def compute():
        today = date.today()

        # Daily load for the last 28 days, aggregated in SQL.
        rows = (
            db.query(Activity.date, func.sum(Activity.duration_min * Activity.rpe))
            .filter(
                Activity.user_id == current_user.id,
                Activity.date >= today - timedelta(days=27),
            )
            .group_by(Activity.date)
            .all()
        )
        daily = {r[0]: float(r[1] or 0) for r in rows}

        # History length needs the earliest date overall, not just the
        # window - one more cheap aggregate rather than a full scan.
        earliest = (
            db.query(func.min(Activity.date))
            .filter(Activity.user_id == current_user.id)
            .scalar()
        )

        def window(days: int) -> List[float]:
            return [daily.get(today - timedelta(days=i), 0.0) for i in range(days)]

        last7 = window(7)
        last28 = window(28)
        acute = sum(last7)
        chronic_weekly = sum(last28) / 4  # 28 days as a weekly average

        history_days = (today - earliest).days + 1 if earliest else 0
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

        # Population standard deviation: these seven days are the whole
        # week, not a sample drawn from it.
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
            # Identical load every single day is MAXIMUM monotony, not an
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

    return _cached(current_user, "load-metrics", compute)


# --- thresholds -----------------------------------------------------------

def _riegel_factor(minutes: float) -> float:
    """How much slower than a short effort's pace the ~60 minute threshold
    pace is likely to be. You can hold a faster pace for 10 minutes than for
    an hour, so a short session overstates fitness unless corrected. Capped
    so a very short sprint cannot produce a wild number."""
    factor = (THRESHOLD_TARGET_MINUTES / minutes) ** RIEGEL_EXPONENT
    return min(factor, RIEGEL_MAX_FACTOR)


@router.get("/thresholds")
def thresholds(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Estimated threshold pace for every distance sport.

    Threshold pace is roughly the effort you could hold for an hour, and it
    is the yardstick the RPE suggestion measures a session against.

    Three tiers, best first:
      personal  - fastest session past this sport's distance/time minimum
      estimated - fastest shorter effort, corrected via Riegel
      default   - a generic value, when there is no history at all

    Only the last THRESHOLD_LOOKBACK_DAYS count, so the number tracks
    current fitness instead of a personal best that may be long gone.
    Every response says which tier it came from and what it was based on,
    so the UI can be honest about how much to trust it.
    """
    def compute():
        cutoff = date.today() - timedelta(days=THRESHOLD_LOOKBACK_DAYS)

        # The tier logic is per-row and sport-specific, so it stays in
        # Python - but the query is narrowed to one sport family inside a
        # 90-day window first, which is a few dozen rows rather than a
        # lifetime of them.
        candidates = (
            db.query(Activity)
            .filter(
                Activity.user_id == current_user.id,
                Activity.date >= cutoff,
                Activity.activity_type.in_(sorted(DISTANCE_TYPES)),
                HAS_DISTANCE,
                Activity.duration_min >= THRESHOLD_SHORT_MIN_MINUTES,
            )
            .all()
        )

        best_full: Dict[str, tuple] = {}
        best_short: Dict[str, tuple] = {}
        counts: Dict[str, int] = {}

        for a in candidates:
            pace = a.duration_min / a.distance_km
            min_km = THRESHOLD_MIN_KM.get(a.activity_type, 3.0)

            if a.duration_min >= THRESHOLD_MIN_MINUTES and a.distance_km >= min_km:
                counts[a.activity_type] = counts.get(a.activity_type, 0) + 1
                current = best_full.get(a.activity_type)
                if current is None or pace < current[0]:
                    best_full[a.activity_type] = (pace, a)
            else:
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

            # Kept so existing clients don't break now that "source"
            # carries the real answer.
            entry["is_default"] = entry["source"] == "default"
            entry["lookback_days"] = THRESHOLD_LOOKBACK_DAYS
            out[sport] = entry

        return out

    return _cached(current_user, "thresholds", compute)


# --- by sport -------------------------------------------------------------

@router.get("/by-sport")
def by_sport(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Totals grouped by sport - powers the per-sport goal tracking."""
    def compute():
        rows = (
            db.query(
                Activity.activity_type,
                func.count(Activity.id),
                func.sum(case((HAS_DISTANCE, Activity.distance_km), else_=0.0)),
                func.sum(Activity.duration_min),
                func.sum(Activity.duration_min * Activity.rpe),
            )
            .filter(Activity.user_id == current_user.id)
            .group_by(Activity.activity_type)
            .all()
        )

        return {
            sport: {
                "activities": count or 0,
                "distance_km": round(float(km or 0), 2),
                "duration_min": round(float(minutes or 0), 1),
                "training_load": round(float(load or 0)),
            }
            for sport, count, km, minutes, load in rows
        }

    return _cached(current_user, "by-sport", compute)
