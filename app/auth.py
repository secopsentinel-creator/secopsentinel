import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload
from starlette.responses import Response

from app.core.config import get_settings
from app.db.session import get_db
from app.models import AuditLog, LoginAttempt, SecurityEvent, User, UserSession

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def utcnow() -> datetime:
    return datetime.now(UTC)


def password_policy_error(password: str) -> str | None:
    settings = get_settings()
    if len(password) < settings.password_min_length:
        return f"La contraseña debe tener al menos {settings.password_min_length} caracteres."
    return None


def hash_password(password: str) -> str:
    error = password_policy_error(password)
    if error:
        raise ValueError(error)
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> tuple[str, UserSession]:
    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)
    session = UserSession(
        token_hash=token_hash(raw_token),
        user_id=user.id,
        expires_at=utcnow() + timedelta(seconds=settings.session_max_age),
    )
    db.add(session)
    db.flush()
    return raw_token, session


def get_session(db: Session, raw_token: str) -> UserSession | None:
    now = utcnow()
    statement = (
        select(UserSession)
        .options(joinedload(UserSession.user))
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.token_hash == token_hash(raw_token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            User.is_active.is_(True),
        )
    )
    return db.scalar(statement)


def revoke_session(db: Session, raw_token: str) -> None:
    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == token_hash(raw_token),
            UserSession.revoked_at.is_(None),
        )
    )
    if session is not None:
        session.revoked_at = utcnow()


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        return None
    session = get_session(db, raw_token)
    if session is None:
        return None
    return session.user


def require_user(user: Annotated[User | None, Depends(get_current_user)]) -> User:
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def set_session_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_max_age,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")


def _csrf_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt="secopsentinel-csrf")


def get_or_create_csrf_token(request: Request, response: Response) -> str:
    settings = get_settings()
    signed = request.cookies.get(settings.csrf_cookie_name)
    if signed:
        try:
            return _csrf_serializer().loads(signed, max_age=settings.session_max_age)
        except (BadSignature, SignatureExpired):
            pass
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=_csrf_serializer().dumps(token),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/",
    )
    return token


def validate_csrf_token(request: Request, form_token: str) -> bool:
    settings = get_settings()
    signed = request.cookies.get(settings.csrf_cookie_name)
    if not signed or not form_token:
        return False
    try:
        cookie_token = _csrf_serializer().loads(signed, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return False
    return hmac.compare_digest(cookie_token, form_token)


def recent_failure_count(db: Session, username: str) -> int:
    settings = get_settings()
    since = utcnow() - timedelta(minutes=settings.login_lockout_minutes)
    last_success = db.scalar(
        select(func.max(LoginAttempt.created_at)).where(
            LoginAttempt.username == username,
            LoginAttempt.success.is_(True),
        )
    )
    if last_success is not None:
        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=UTC)
        if last_success > since:
            since = last_success
    statement = select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.username == username,
        LoginAttempt.success.is_(False),
        LoginAttempt.created_at >= since,
    )
    return int(db.scalar(statement) or 0)


def is_login_locked(db: Session, username: str) -> bool:
    settings = get_settings()
    return recent_failure_count(db, username) >= settings.login_max_attempts


def record_login_attempt(
    db: Session,
    username: str,
    success: bool,
    request: Request,
) -> LoginAttempt:
    attempt = LoginAttempt(
        username=username,
        success=success,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(attempt)
    return attempt


def write_audit(
    db: Session,
    action: str,
    entity: str,
    username: str | None = None,
    user_id: int | None = None,
    entity_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            username=username,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
        )
    )


def write_security_event(
    db: Session,
    event_type: str,
    severity: str,
    detail: str,
) -> None:
    db.add(
        SecurityEvent(
            event_type=event_type,
            severity=severity,
            detail=detail,
        )
    )
