from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.feedback import FeedbackCreateRequest, FeedbackOut
from app.services import feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/hour")
async def submit_feedback(
    request: Request,
    payload: FeedbackCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackOut:
    try:
        feedback = await feedback_service.submit_feedback(
            db, user=current_user, category=payload.category, message=payload.message, app_version=payload.app_version,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    return FeedbackOut(
        id=feedback.id,
        category=feedback.category,
        message=feedback.message,
        app_version=feedback.app_version,
        created_at=feedback.created_at.isoformat(),
    )
