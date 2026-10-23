"""The committed docs/openapi.json must match what the app generates."""

import json
from pathlib import Path

from analysis.main import create_app

COMMITTED = Path(__file__).parents[2] / "docs" / "openapi.json"


def test_openapi_document_is_committed_and_current():
    generated = create_app(run_worker=False).openapi()
    assert COMMITTED.exists(), "run: python -m analysis.export_openapi > docs/openapi.json"
    assert json.loads(COMMITTED.read_text()) == generated, (
        "docs/openapi.json is stale; regenerate with "
        "`python -m analysis.export_openapi > docs/openapi.json` (from analysis/)"
    )


def test_openapi_documents_the_expected_surface():
    spec = create_app(run_worker=False).openapi()
    paths = spec["paths"]
    assert set(paths["/api/v1/rules"]) == {"get", "post"}
    assert set(paths["/api/v1/rules/{rule_id}"]) == {"get", "put", "delete"}
    assert set(paths["/api/v1/alerts"]) == {"get"}
    assert "post" in paths["/api/v1/alerts/{alert_id}/ack"]
    for p in ("register", "login", "logout"):
        assert set(paths[f"/api/v1/auth/{p}"]) == {"post"}
    assert set(paths["/api/v1/auth/me"]) == {"get"}
    assert set(paths["/api/v1/users"]) == {"get"}
    assert set(paths["/api/v1/users/{user_id}"]) == {"patch"}
    assert "401" in paths["/api/v1/alerts"]["get"]["responses"]
    assert "403" in paths["/api/v1/rules"]["post"]["responses"]
    assert {p["name"] for p in paths["/api/v1/alerts"]["get"]["parameters"]} >= {
        "deviceId",
        "severity",
        "state",
        "ruleId",
        "since",
        "until",
        "limit",
        "offset",
    }
