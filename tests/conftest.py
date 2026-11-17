import os
import re

os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["PASSWORD_MIN_LENGTH"] = "12"
os.environ["LOGIN_MAX_ATTEMPTS"] = "5"
os.environ["LOGIN_LOCKOUT_MINUTES"] = "15"
os.environ["COOKIE_SECURE"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import User


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("CASES_DIR", str(data_dir / "cases"))
    monkeypatch.setenv("QUARANTINE_DIR", str(data_dir / "quarantine"))
    monkeypatch.setenv("DOCUMENTS_DIR", str(data_dir / "documents"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def user(db_session):
    user = User(
        username="maudaemon",
        password_hash=hash_password("ClaveSegura123"),
        role="investigador",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(client, user):
    response = client.get("/login")
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    response = client.post(
        "/login",
        data={
            "username": "maudaemon",
            "password": "ClaveSegura123",
            "csrf_token": match.group(1),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client
