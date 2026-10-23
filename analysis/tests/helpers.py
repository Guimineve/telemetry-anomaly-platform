"""App and logged-in clients for HTTP tests, backed by the in-memory store."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from analysis.auth.passwords import hash_password
from analysis.config import Settings
from analysis.main import create_app

from .fakes import FakeStore

SECRET = "test-secret-that-is-at-least-32-characters"
PASSWORD = "correct-horse-battery"


def make_app(**settings):
    app = create_app(Settings(jwt_secret=SECRET, **settings), run_worker=False)
    store = FakeStore()
    app.state.store = store
    return app, store


def add_user(store: FakeStore, username: str, role: str = "viewer", password: str = PASSWORD):
    return asyncio.run(store.create_user(username, hash_password(password), role))


def login(app, username: str, password: str = PASSWORD) -> TestClient:
    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return client
