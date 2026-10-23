import json

from fastapi.testclient import TestClient

from .helpers import PASSWORD, add_user, login, make_app

RULE = {"name": "t", "type": "threshold", "metric": "temperature", "params": {"max": 60}}
NEW = "a-brand-new-password"


def actions(store):
    return [e.action for e in store.events]


def test_security_events_are_recorded_without_secrets():
    app, store = make_app()
    root = add_user(store, "root", role="admin")
    anon = TestClient(app)
    anon.post("/api/v1/auth/login", json={"username": "root", "password": "wrong-password"})
    anon.post("/api/v1/auth/login", json={"username": "ghost", "password": "wrong-password"})
    admin = login(app, "root")
    vic = TestClient(app)
    vic.post("/api/v1/auth/register", json={"username": "vic", "password": PASSWORD})
    vic_id = next(u.id for u in store.users.values() if u.username == "vic")

    rule = admin.post("/api/v1/rules", json=RULE).json()
    admin.put(f"/api/v1/rules/{rule['id']}", json={**RULE, "enabled": False})
    admin.delete(f"/api/v1/rules/{rule['id']}")
    admin.patch(f"/api/v1/users/{vic_id}", json={"role": "admin"})
    admin.post(f"/api/v1/users/{vic_id}/password", json={"newPassword": NEW})
    admin.post("/api/v1/auth/password", json={"currentPassword": PASSWORD, "newPassword": NEW})
    admin.post("/api/v1/auth/logout-all")

    assert actions(store) == [
        "login.failure",
        "login.failure",
        "login.success",
        "user.register",
        "rule.create",
        "rule.update",
        "rule.delete",
        "user.update",
        "user.password_reset",
        "user.password_change",
        "session.logout_all",
    ]
    by_action = {e.action: e for e in store.events}
    failed_unknown = store.events[1]
    assert failed_unknown.actor_id is None and failed_unknown.actor_username == "ghost"
    assert by_action["login.success"].actor_id == root.id
    assert by_action["rule.create"].target_id == rule["id"]
    assert by_action["rule.create"].detail == {"name": "t"}
    assert by_action["user.update"].target_id == vic_id
    assert by_action["user.update"].detail == {"role": "admin"}
    assert by_action["user.register"].actor_username == "vic"
    assert all(e.ip for e in store.events)

    # nothing secret ends up in the log
    dump = json.dumps([vars_of(e) for e in store.events], default=str)
    for secret in (PASSWORD, NEW, "wrong-password", "session="):
        assert secret not in dump


def vars_of(e):
    return {k: v for k, v in vars(e).items() if not k.startswith("_")}


def test_audit_api_is_admin_only_and_filterable():
    app, store = make_app()
    add_user(store, "root", role="admin")
    add_user(store, "vic")
    viewer = login(app, "vic")
    admin = login(app, "root")
    TestClient(app).post("/api/v1/auth/login", json={"username": "vic", "password": "nope-nope"})

    assert viewer.get("/api/v1/audit").status_code == 403
    assert TestClient(app).get("/api/v1/audit").status_code == 401

    page = admin.get("/api/v1/audit", params={"limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert page["items"][0]["action"] == "login.failure"  # newest first
    assert set(page["items"][0]) >= {"id", "ts", "actorUsername", "action", "ip", "detail"}
    failures = admin.get("/api/v1/audit", params={"action": "login.failure"}).json()
    assert failures["total"] == 1
    by_root = admin.get("/api/v1/audit", params={"actor": "root"}).json()
    assert [e["action"] for e in by_root["items"]] == ["login.success"]
    assert admin.get("/api/v1/audit", params={"limit": 500}).status_code == 422
