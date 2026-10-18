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
