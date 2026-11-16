import re

import pytest
from sqlalchemy import func, select

from app.auth import hash_password, password_policy_error
from app.models import AuditLog, LoginAttempt, SecurityEvent


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_password_policy_rejects_short_password():
    assert password_policy_error("corta") is not None
    with pytest.raises(ValueError):
        hash_password("corta")


def test_dashboard_requires_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_success_and_logout(client, user, db_session):
    response = client.get("/login")
    assert response.status_code == 200
    token = csrf_from(response.text)

    response = client.post(
        "/login",
        data={
            "username": "maudaemon",
            "password": "ClaveSegura123",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "httponly" in response.headers["set-cookie"].lower()

    response = client.get("/")
    assert response.status_code == 200
    assert "maudaemon" in response.text
    token = csrf_from(response.text)

    response = client.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303

    login_audit = db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "login")
    )
    logout_audit = db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "logout")
    )
    assert login_audit == 1
    assert logout_audit == 1


def test_login_rejects_missing_csrf(client, user):
    response = client.post(
        "/login",
        data={
            "username": "maudaemon",
            "password": "ClaveSegura123",
            "csrf_token": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_login_failure_and_lockout(client, user, db_session):
    response = client.get("/login")
    token = csrf_from(response.text)

    for _ in range(5):
        response = client.post(
            "/login",
            data={
                "username": "maudaemon",
                "password": "incorrecta",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 401

    response = client.post(
        "/login",
        data={
            "username": "maudaemon",
            "password": "ClaveSegura123",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 429

    failures = db_session.scalar(select(func.count()).select_from(LoginAttempt))
    lockouts = db_session.scalar(
        select(func.count())
        .select_from(SecurityEvent)
        .where(SecurityEvent.event_type == "login_lockout")
    )
    assert failures == 5
    assert lockouts == 1


def test_failures_reset_after_success(client, user):
    response = client.get("/login")
    token = csrf_from(response.text)

    for _ in range(4):
        response = client.post(
            "/login",
            data={
                "username": "maudaemon",
                "password": "incorrecta",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 401

    response = client.post(
        "/login",
        data={
            "username": "maudaemon",
            "password": "ClaveSegura123",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    for _ in range(4):
        response = client.post(
            "/login",
            data={
                "username": "maudaemon",
                "password": "incorrecta",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 401

    response = client.post(
        "/login",
        data={
            "username": "maudaemon",
            "password": "ClaveSegura123",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_security_headers(client):
    response = client.get("/login")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
