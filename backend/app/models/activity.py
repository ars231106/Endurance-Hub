from datetime import date as date_type

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String

from app.models.base import Base


class Activity(Base):
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True, index=True)
    # Foreign key: every activity belongs to exactly one user.
    # Indexed because "show me MY activities" filters on this constantly.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    activity_type = Column(String, nullable=False)
    # Nullable: strength work, sports and classes have no meaningful
    # distance, so only distance-based sports fill this in.
    distance_km = Column(Float, nullable=True)
    duration_min = Column(Float, nullable=False)
    # RPE = Rate of Perceived Exertion (1-10). Every activity has one,
    # which is what lets training load compare a run to a gym session.
    rpe = Column(Integer, nullable=False)
    # Average heart rate for the session, if the user wears a monitor.
    # Optional everywhere - it supplements RPE rather than replacing it.
    avg_hr = Column(Integer, nullable=True)
    date = Column(Date, nullable=False, default=date_type.today, index=True)
    notes = Column(String, nullable=True)
