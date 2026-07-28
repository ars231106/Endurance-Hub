from datetime import date as date_type
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.constants import ACTIVITY_TYPES, DISTANCE_TYPES


class ActivityCreate(BaseModel):
    activity_type: str = Field(min_length=1, max_length=50)
    # Optional: only distance-based sports supply it. The validator below
    # enforces which ones must.
    distance_km: Optional[float] = Field(default=None, gt=0, le=1000)
    duration_min: float = Field(gt=0, le=10080)
    rpe: int = Field(ge=1, le=10)
    # Optional wearable data: average heart rate for the session.
    avg_hr: Optional[int] = Field(default=None, ge=30, le=230)
    date: Optional[date_type] = None  # defaults to today if omitted
    notes: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_type_and_distance(self):
        if self.activity_type not in ACTIVITY_TYPES:
            raise ValueError(f"Unknown activity type '{self.activity_type}'")
        if self.activity_type in DISTANCE_TYPES:
            if self.distance_km is None:
                raise ValueError(f"'{self.activity_type}' requires a distance in km")
        else:
            # Strength work and sports never store a distance, even if a
            # client sends one.
            self.distance_km = None
        return self


class ActivityUpdate(BaseModel):
    # Everything optional: the client sends only the fields it wants to change
    # (partial update), and we apply just those.
    activity_type: Optional[str] = Field(default=None, min_length=1, max_length=50)
    distance_km: Optional[float] = Field(default=None, gt=0, le=1000)
    duration_min: Optional[float] = Field(default=None, gt=0, le=10080)
    rpe: Optional[int] = Field(default=None, ge=1, le=10)
    avg_hr: Optional[int] = Field(default=None, ge=30, le=230)
    date: Optional[date_type] = None
    notes: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_type(self):
        if self.activity_type is not None and self.activity_type not in ACTIVITY_TYPES:
            raise ValueError(f"Unknown activity type '{self.activity_type}'")
        return self


class ActivityOut(BaseModel):
    id: int
    activity_type: str
    distance_km: Optional[float] = None
    duration_min: float
    rpe: int
    avg_hr: Optional[int] = None
    date: date_type
    notes: Optional[str] = None

    model_config = {"from_attributes": True}
