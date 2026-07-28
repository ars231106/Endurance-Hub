from datetime import date as date_type
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.constants import DISTANCE_TYPES
from app.databases.database import get_db
from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.user import User
from app.schemas.activity_schemas import ActivityCreate, ActivityOut, ActivityUpdate

router = APIRouter(prefix="/activities", tags=["activities"])


def get_owned_activity(activity_id: int, db: Session, user: User) -> Activity:
    # Filtering by BOTH id and user_id means users can never touch each
    # other's activities - a wrong owner looks identical to "not found".
    activity = (
        db.query(Activity)
        .filter(Activity.id == activity_id, Activity.user_id == user.id)
        .first()
    )
    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )
    return activity


@router.post("", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
def create_activity(
    payload: ActivityCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # user_id comes from the verified token, never from the request body -
    # clients cannot log activities as someone else.
    activity = Activity(
        user_id=current_user.id,
        activity_type=payload.activity_type,
        distance_km=payload.distance_km,
        duration_min=payload.duration_min,
        rpe=payload.rpe,
        avg_hr=payload.avg_hr,
        date=payload.date or date_type.today(),
        notes=payload.notes,
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


@router.get("", response_model=List[ActivityOut])
def list_activities(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Newest first, with limit/offset pagination so the response stays
    # small even after years of training data.
    return (
        db.query(Activity)
        .filter(Activity.user_id == current_user.id)
        .order_by(Activity.date.desc(), Activity.id.desc())
        .offset(offset)
        .limit(min(limit, 200))
        .all()
    )


@router.get("/{activity_id}", response_model=ActivityOut)
def get_activity(
    activity_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_owned_activity(activity_id, db, current_user)


@router.put("/{activity_id}", response_model=ActivityOut)
def update_activity(
    activity_id: int,
    payload: ActivityUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    activity = get_owned_activity(activity_id, db, current_user)

    # exclude_unset -> only fields the client actually sent are applied,
    # so a partial update never wipes the other columns.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(activity, field, value)

    # Switching type can invalidate the distance, so re-check the pairing
    # against the final state of the row.
    if activity.activity_type in DISTANCE_TYPES:
        if activity.distance_km is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{activity.activity_type}' requires a distance in km",
            )
    else:
        activity.distance_km = None

    db.commit()
    db.refresh(activity)
    return activity


@router.delete("/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_activity(
    activity_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    activity = get_owned_activity(activity_id, db, current_user)
    db.delete(activity)
    db.commit()
    # 204 No Content: success with an intentionally empty response body.
