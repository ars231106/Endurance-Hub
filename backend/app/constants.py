"""Single source of truth for what counts as an activity.

The frontend mirrors this list. Categories decide the shape of a session:
only DISTANCE sports record kilometres - everything else is logged with
duration and effort alone, because distance is meaningless for a squat
session or a football match.
"""

# activity_type -> category
ACTIVITY_TYPES = {
    # distance based: km + duration + RPE
    "run": "distance",
    "ride": "distance",
    "swim": "distance",
    "row": "distance",
    "hike": "distance",
    "walk": "distance",
    # strength & conditioning: duration + RPE
    "strength": "strength",
    "crossfit": "strength",
    "calisthenics": "strength",
    "hiit": "strength",
    # sports: duration + RPE
    "football": "sport",
    "basketball": "sport",
    "cricket": "sport",
    "tennis": "sport",
    "badminton": "sport",
    "tabletennis": "sport",
    "volleyball": "sport",
    "hockey": "sport",
    "rugby": "sport",
    "baseball": "sport",
    "golf": "sport",
    "boxing": "sport",
    "martialarts": "sport",
    "climbing": "sport",
    "skiing": "sport",
    # everything else: duration + RPE
    "yoga": "other",
    "pilates": "other",
    "other": "other",
}

DISTANCE_TYPES = {t for t, cat in ACTIVITY_TYPES.items() if cat == "distance"}


def is_distance_based(activity_type: str) -> bool:
    return activity_type in DISTANCE_TYPES


# What counts as a "sustained effort" worth estimating threshold pace from.
# Sport-specific because 3 km is a warm-up on a bike and a long way in a
# pool - one global minimum would exclude swimmers entirely.
THRESHOLD_MIN_KM = {
    "run": 3.0,
    "ride": 8.0,
    "swim": 0.4,
    "row": 1.0,
    "hike": 2.0,
    "walk": 2.0,
}
THRESHOLD_MIN_MINUTES = 15

# Only recent sessions count toward the estimate. Without a window, a
# personal best from a year ago would keep the threshold optimistic
# forever, so every session after a break would be scored as too easy.
THRESHOLD_LOOKBACK_DAYS = 90

# Shorter efforts still say something about fitness, so they're used as a
# second-choice estimate rather than being thrown away - otherwise a
# beginner whose longest run is 2 km would never get a personal number.
THRESHOLD_SHORT_MIN_MINUTES = 8

# Riegel (1981): performance times scale with distance by a power law.
# Rearranged for pace, a shorter effort implies a slightly slower pace
# over the ~60 minutes that threshold represents. A 20-minute effort
# implies roughly 7% slower; a 10-minute effort roughly 11%.
THRESHOLD_TARGET_MINUTES = 60
RIEGEL_EXPONENT = 0.06
# Ceiling so a very short, very fast effort can't produce a wild estimate.
RIEGEL_MAX_FACTOR = 1.15

# Last resort, in minutes per km, for a sport with no history at all.
# Roughly a moderately trained recreational athlete; the API labels these
# so the UI can admit the number isn't personal yet.
DEFAULT_THRESHOLD_PACE = {
    "run": 5.5,     # 5:30 /km
    "ride": 2.0,    # 30 km/h
    "swim": 20.0,   # 2:00 /100m
    "row": 4.0,     # 2:00 /500m
    "hike": 12.0,
    "walk": 10.0,
}
