from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from analysis.auth.passwords import hash_password, verify_password
from analysis.auth.ratelimit import AttemptLimiter
from analysis.auth.tokens import TokenCodec
from analysis.config import Settings
from analysis.db import RuleRow
from analysis.main import bootstrap_admin, check_settings

from .fakes import FakeStore
from .helpers import PASSWORD, SECRET, add_user, login, make_app

DB = "postgresql+asyncpg://u:p@db/x"
RULE = {"name": "t", "type": "threshold", "metric": "temperature", "params": {"max": 60}}


# -- pure pieces -----------------------------------------------------------
def test_password_hash_roundtrip():
    h = hash_password("s3cret-password")
    assert h.startswith("$argon2id$") and "s3cret" not in h
    assert verify_password(h, "s3cret-password")
    assert not verify_password(h, "wrong-password")
    assert not verify_password(None, "anything")  # unknown user
    assert not verify_password("not-a-hash", "anything")


def test_token_roundtrip_expiry_and_forgery():
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    codec = TokenCodec(SECRET, ttl_s=60, clock=lambda: now[0])
    token = codec.issue("user-1")
    assert codec.decode(token) == "user-1"
    now[0] += timedelta(seconds=59)
    assert codec.decode(token) == "user-1"
    now[0] += timedelta(seconds=1)
    assert codec.decode(token) is None  # expired

    other = TokenCodec("another-secret-that-is-32-chars-long!", ttl_s=60, clock=lambda: now[0])
    assert codec.decode(other.issue("user-1")) is None  # wrong signature
    assert codec.decode("garbage") is None
    no_sub = jwt.encode({"exp": 9_999_999_999}, SECRET, algorithm="HS256")
    assert codec.decode(no_sub) is None
    none_alg = jwt.encode({"sub": "user-1", "exp": 9_999_999_999}, None, algorithm="none")
    assert codec.decode(none_alg) is None


def test_attempt_limiter_window_and_reset():
    t = [0.0]
    lim = AttemptLimiter(max_attempts=3, window_s=60, clock=lambda: t[0])
    for _ in range(3):
        assert not lim.blocked("a")
        lim.record("a", "b")
    assert lim.blocked("a") and lim.blocked("x", "b") and not lim.blocked("x")
    t[0] = 60.0
    assert not lim.blocked("a")  # window passed
    lim.record("a", "a", "a")
    lim.reset("a")
    assert not lim.blocked("a")


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"jwt_secret": "short"}, "JWT_SECRET"),
        ({"admin_username": "admin"}, "both"),
        ({"admin_username": "admin", "admin_password": "short"}, "ADMIN_PASSWORD"),
        ({"cookie_secure": "maybe"}, "COOKIE_SECURE"),
        ({"database_url": ""}, "DATABASE_URL"),
    ],
)
def test_bad_auth_settings_fail_fast(overrides, message):
    with pytest.raises(RuntimeError, match=message):
        check_settings(Settings(**{"jwt_secret": SECRET, "database_url": DB, **overrides}))


def test_good_auth_settings_pass():
    check_settings(Settings(jwt_secret=SECRET, database_url=DB))
    check_settings(
        Settings(jwt_secret=SECRET, database_url=DB, admin_username="a", admin_password=PASSWORD)
    )


async def test_bootstrap_admin_creates_once_and_never_overwrites():
    store = FakeStore()
    settings = Settings(jwt_secret=SECRET, admin_username="boss", admin_password=PASSWORD)
    assert await bootstrap_admin(store, settings) is True
    user = await store.get_user_by_username("boss")
    assert user.role == "admin" and verify_password(user.password_hash, PASSWORD)

    changed = Settings(jwt_secret=SECRET, admin_username="boss", admin_password="new-password!")
    assert await bootstrap_admin(store, changed) is False
    assert verify_password(user.password_hash, PASSWORD)  # untouched
    assert await bootstrap_admin(store, Settings(jwt_secret=SECRET)) is False
    assert len(store.users) == 1


# -- registration and login ------------------------------------------------
def test_register_creates_a_logged_in_viewer():
    app, store = make_app()
    c = TestClient(app)
    r = c.post(
        "/api/v1/auth/register",
        json={"username": "  Alice ", "password": PASSWORD, "role": "admin"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "alice" and body["role"] == "viewer"  # role field is ignored
    assert "passwordHash" not in body and "password" not in body
    cookie = r.headers["set-cookie"].lower()
    assert "session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie  # plain http in tests
    assert c.get("/api/v1/auth/me").json()["username"] == "alice"

    dup = TestClient(app).post(
        "/api/v1/auth/register", json={"username": "ALICE", "password": PASSWORD}
    )
    assert dup.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"username": "al", "password": PASSWORD},  # too short
        {"username": "has space", "password": PASSWORD},
        {"username": "x" * 33, "password": PASSWORD},
        {"username": "bob", "password": "short"},
        {"username": "bob", "password": "x" * 129},
        {"username": "bob"},
    ],
)
def test_register_validates_input(body):
    app, store = make_app()
    assert TestClient(app).post("/api/v1/auth/register", json=body).status_code == 422
    assert not store.users


def test_registration_is_rate_limited_per_client():
    app, _ = make_app()
    c = TestClient(app)
    codes = [
        c.post("/api/v1/auth/register", json={"username": f"user{i}", "password": PASSWORD})
        .status_code
        for i in range(11)
    ]
    assert codes[:10] == [201] * 10 and codes[10] == 429


def test_login_errors_do_not_reveal_which_part_was_wrong():
    app, store = make_app()
    add_user(store, "alice")
    c = TestClient(app)
    wrong_pw = c.post("/api/v1/auth/login", json={"username": "alice", "password": "nope"})
    unknown = c.post("/api/v1/auth/login", json={"username": "nobody", "password": "nope"})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()
    assert "set-cookie" not in wrong_pw.headers

    ok = c.post("/api/v1/auth/login", json={"username": "ALICE", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["lastLoginAt"] is not None


def test_disabled_user_cannot_log_in():
    app, store = make_app()
    add_user(store, "alice").is_active = False
    r = TestClient(app).post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 401


def test_login_is_rate_limited_after_repeated_failures():
    app, store = make_app()
    add_user(store, "alice")
    c = TestClient(app)
    bad = {"username": "alice", "password": "wrong-password"}
    assert [c.post("/api/v1/auth/login", json=bad).status_code for _ in range(5)] == [401] * 5
    # even the right password is refused while blocked
    good = {"username": "alice", "password": PASSWORD}
    assert c.post("/api/v1/auth/login", json=good).status_code == 429


def test_logout_clears_the_session():
    app, store = make_app()
    add_user(store, "alice")
    c = login(app, "alice")
    assert c.get("/api/v1/auth/me").status_code == 200
    r = c.post("/api/v1/auth/logout")
    assert r.status_code == 204 and "session=" in r.headers["set-cookie"]
    assert c.get("/api/v1/auth/me").status_code == 401


def test_secure_cookie_when_configured():
    app, store = make_app(cookie_secure="true")
    add_user(store, "alice")
    r = TestClient(app).post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


def test_forged_or_unknown_session_is_rejected():
    app, store = make_app()
    c = TestClient(app)
    c.cookies.set("session", "not-a-token")
    assert c.get("/api/v1/auth/me").status_code == 401
    c.cookies.set("session", app.state.tokens.issue("no-such-user"))
    assert c.get("/api/v1/auth/me").status_code == 401


# -- permissions -----------------------------------------------------------
def test_everything_but_health_requires_a_session():
    app, _ = make_app()
    c = TestClient(app)
    for path in ("/api/v1/rules", "/api/v1/alerts", "/api/v1/alerts/x", "/api/v1/users"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/v1/rules", json=RULE).status_code == 401
    assert c.post("/api/v1/alerts/x/ack").status_code == 401
    assert c.get("/health").status_code == 200
    assert c.get("/openapi.json").status_code == 200


def test_viewer_reads_and_acks_but_cannot_change_rules():
    app, store = make_app()
    add_user(store, "vic")
    alert = store.add_alert()
    store.rules["r1"] = RuleRow(
        id="r1", device_pattern="*", severity="warning", enabled=True, **RULE
    )
    c = login(app, "vic")
    assert c.get("/api/v1/rules").status_code == 200
    assert c.get("/api/v1/alerts").json()["total"] == 1
    assert c.post(f"/api/v1/alerts/{alert.id}/ack").status_code == 200
    assert c.post("/api/v1/rules", json=RULE).status_code == 403
    assert c.put("/api/v1/rules/r1", json=RULE).status_code == 403
    assert c.delete("/api/v1/rules/r1").status_code == 403
    assert c.get("/api/v1/users").status_code == 403
    assert "r1" in store.rules


def test_role_change_and_disable_apply_to_existing_sessions():
    app, store = make_app()
    vic = add_user(store, "vic")
    c = login(app, "vic")
    assert c.post("/api/v1/rules", json=RULE).status_code == 403
    vic.role = "admin"
    assert c.post("/api/v1/rules", json=RULE).status_code == 201
    vic.is_active = False
    assert c.get("/api/v1/rules").status_code == 401


# -- user management -------------------------------------------------------
def test_admin_manages_users():
    app, store = make_app()
    root = add_user(store, "root", role="admin")
    vic = add_user(store, "vic")
    c = login(app, "root")

    users = c.get("/api/v1/users").json()
    assert [u["username"] for u in users] == ["root", "vic"]
    assert all("passwordHash" not in u for u in users)

    r = c.patch(f"/api/v1/users/{vic.id}", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    r = c.patch(f"/api/v1/users/{vic.id}", json={"isActive": False})
    assert r.status_code == 200 and r.json()["isActive"] is False

    assert c.patch(f"/api/v1/users/{root.id}", json={"role": "viewer"}).status_code == 409
    assert c.patch(f"/api/v1/users/{root.id}", json={"isActive": False}).status_code == 409
    assert c.patch(f"/api/v1/users/{root.id}", json={"isActive": True}).status_code == 200
    assert c.patch("/api/v1/users/nope", json={"role": "viewer"}).status_code == 404
    assert c.patch(f"/api/v1/users/{vic.id}", json={"role": "owner"}).status_code == 422
    assert root.role == "admin" and root.is_active


# -- WebSocket -------------------------------------------------------------
def test_websocket_requires_a_session():
    app, _ = make_app()
    with pytest.raises(WebSocketDisconnect) as exc, TestClient(app).websocket_connect("/ws/alerts"):
        pass
    assert exc.value.code == 1008


def test_websocket_rejects_cross_site_origin():
    app, store = make_app()
    add_user(store, "alice")
    c = login(app, "alice")
    with c.websocket_connect("/ws/alerts", headers={"origin": "http://testserver"}) as ws:
        ws.send_text("ping")
        assert ws.receive_text() == "pong"
    with pytest.raises(WebSocketDisconnect), c.websocket_connect(
        "/ws/alerts", headers={"origin": "http://evil.example"}
    ):
        pass


def test_websocket_rejects_disabled_user():
    app, store = make_app()
    add_user(store, "alice").is_active = False
    c = TestClient(app)
    c.cookies.set("session", app.state.tokens.issue(next(iter(store.users))))
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws/alerts"):
        pass
