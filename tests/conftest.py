import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models.user import User, UserRole

DEFAULT_TEST_PASSWORD = "correct-horse-1"


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def web():
    """Like `client` above, but also exposes the underlying session so tests can seed
    Users/Clients/etc. the web UI itself has no way to create (users only ever come from
    the CRM sync — see app/services/sync.py) — shared by test_dashboard.py, test_auth.py,
    and test_admin.py, all of which need to seed data behind a logged-in session."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client, session
    app.dependency_overrides.clear()
    session.close()


def make_user(
    session,
    *,
    id,
    name,
    role=UserRole.developer,
    username=None,
    password=None,
    must_change_password=False,
    machine_group_id=None,
):
    """Seed a User, optionally with login access (password set + hashed). Login-capable
    users default to must_change_password=False so tests can log in and act immediately
    without an extra round-trip through /change-password, unless a test is specifically
    exercising that forced flow."""
    user = User(id=id, name=name, role=role, username=username, machine_group_id=machine_group_id)
    if password is not None:
        user.password_hash = hash_password(password)
        user.must_change_password = must_change_password
    session.add(user)
    session.flush()
    return user


def login_as(client, username, password=DEFAULT_TEST_PASSWORD):
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303, f"login as {username!r} failed: {response.text}"
    return response
