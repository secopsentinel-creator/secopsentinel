from datetime import date, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.core.templating import APP_DIR, render_page
from app.db.session import get_db
from app.models import Case, User
from app.routers import auth, cases

app = FastAPI(
    title="secopsentinel",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.include_router(auth.router)
app.include_router(cases.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self'; "
        "script-src 'self'; "
        "img-src 'self' data:; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def dashboard(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    today = date.today()
    case_count = int(db.scalar(select(func.count()).select_from(Case)) or 0)
    due_soon_count = int(
        db.scalar(
            select(func.count())
            .select_from(Case)
            .where(
                Case.due_date.is_not(None),
                Case.due_date >= today,
                Case.due_date <= today + timedelta(days=7),
                Case.status != "archivado",
            )
        )
        or 0
    )
    recent_cases = list(
        db.scalars(select(Case).order_by(Case.created_at.desc()).limit(5)).all()
    )
    return render_page(
        request,
        "dashboard.html",
        {
            "user": user,
            "today": today,
            "case_count": case_count,
            "due_soon_count": due_soon_count,
            "recent_cases": recent_cases,
        },
    )
