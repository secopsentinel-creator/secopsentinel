from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth import (
    authenticate,
    clear_session_cookie,
    create_session,
    get_current_user,
    is_login_locked,
    recent_failure_count,
    record_login_attempt,
    revoke_session,
    set_session_cookie,
    validate_csrf_token,
    write_audit,
    write_security_event,
)
from app.core.config import get_settings
from app.core.templating import render_page
from app.db.session import get_db
from app.schemas import LoginForm

router = APIRouter()


@router.get("/login")
def login_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    if get_current_user(request, db) is not None:
        return RedirectResponse("/", status_code=303)
    return render_page(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
):
    settings = get_settings()
    if not validate_csrf_token(request, csrf_token):
        return render_page(
            request,
            "login.html",
            {"error": "Solicitud inválida. Recargue la página."},
            status_code=400,
        )
    try:
        form = LoginForm(
            username=username,
            password=password,
            csrf_token=csrf_token,
        )
    except ValidationError:
        return render_page(
            request,
            "login.html",
            {"error": "Datos de ingreso inválidos."},
            status_code=400,
        )

    if is_login_locked(db, form.username):
        client_host = request.client.host if request.client else "desconocido"
        write_security_event(
            db,
            event_type="login_blocked",
            severity="warning",
            detail=f"Login bloqueado para {form.username} desde {client_host}",
        )
        write_audit(
            db,
            action="login_blocked",
            entity="user",
            username=form.username,
            entity_id=form.username,
        )
        db.commit()
        return render_page(
            request,
            "login.html",
            {"error": "Demasiados intentos fallidos. Intente más tarde."},
            status_code=429,
        )

    user = authenticate(db, form.username, form.password)
    if user is None:
        record_login_attempt(db, form.username, False, request)
        write_audit(
            db,
            action="login_failed",
            entity="user",
            username=form.username,
            entity_id=form.username,
        )
        db.flush()
        failures = recent_failure_count(db, form.username)
        if failures >= settings.login_max_attempts:
            write_security_event(
                db,
                event_type="login_lockout",
                severity="warning",
                detail=f"Usuario {form.username} bloqueado tras {failures} intentos fallidos.",
            )
        db.commit()
        return render_page(
            request,
            "login.html",
            {"error": "Usuario o contraseña incorrectos."},
            status_code=401,
        )

    raw_token, user_session = create_session(db, user)
    record_login_attempt(db, form.username, True, request)
    write_audit(
        db,
        action="login",
        entity="session",
        username=user.username,
        user_id=user.id,
        entity_id=str(user_session.id),
    )
    db.commit()

    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, raw_token)
    return response


@router.post("/logout")
def logout(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    csrf_token: Annotated[str, Form()] = "",
):
    settings = get_settings()
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=400, detail="Solicitud inválida.")
    user = get_current_user(request, db)
    raw_token = request.cookies.get(settings.session_cookie_name)
    if user is not None:
        if raw_token:
            revoke_session(db, raw_token)
        write_audit(
            db,
            action="logout",
            entity="session",
            username=user.username,
            user_id=user.id,
        )
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response
