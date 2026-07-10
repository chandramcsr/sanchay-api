from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.auth import (
    DeleteAccountRequest,
    ForgotPasswordRequest,
    LoginEventOut,
    LoginRequest,
    ReconnectedHistory,
    RefreshRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
    VerifyEmailRequest,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def signup(
    request: Request, payload: SignupRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    access_token, refresh_token, user, reconnect_summary = await auth_service.signup(
        db, background_tasks, email=payload.email, password=payload.password, display_name=payload.display_name
    )
    reconnected_history = (
        ReconnectedHistory(groups_reconnected=reconnect_summary["groups_reconnected"], total_amount=str(reconnect_summary["total_amount"]))
        if reconnect_summary["groups_reconnected"] > 0
        else None
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut.model_validate(user),
        reconnected_history=reconnected_history,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    access_token, refresh_token, user = await auth_service.login(db, request, email=payload.email, password=payload.password)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserOut.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/hour")
async def refresh(request: Request, payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    access_token, refresh_token, user = await auth_service.refresh(db, refresh_token=payload.refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def read_current_user(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit("3/hour")
async def forgot_password(
    request: Request, payload: ForgotPasswordRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    await auth_service.forgot_password(db, background_tasks, email=payload.email)
    # Always the same generic response whether or not the email is
    # registered — confirming existence via this endpoint is itself
    # an account-enumeration leak, same principle as login.
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password", response_model=TokenResponse)
@limiter.limit("5/hour")
async def reset_password(request: Request, payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    access_token, refresh_token, user = await auth_service.reset_password(db, token=payload.token, new_password=payload.new_password)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserOut.model_validate(user))


@router.get("/login-history", response_model=list[LoginEventOut])
async def login_history(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LoginEventOut]:
    return await auth_service.get_login_history(db, current_user=current_user, limit=limit)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("3/hour")
async def delete_account(
    request: Request,
    payload: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await auth_service.delete_account(db, current_user=current_user, password=payload.password)


@router.post("/verify-email", response_model=UserOut)
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)) -> User:
    return await auth_service.verify_email(db, token=payload.token)


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
@limiter.limit("3/hour")
async def resend_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    message = await auth_service.resend_verification(db, background_tasks, current_user=current_user)
    return {"message": message}
