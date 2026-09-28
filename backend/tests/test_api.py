"""API surface tests.

Thin on purpose -- the reconciliation logic is covered in
test_reconciliation.py. These check that the HTTP layer exposes it correctly
and that the filters actually filter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_stats_reports_the_full_ingest(client):
    stats = client.get("/api/stats").json()
    assert stats["total_records_read"] == 42
    assert stats["unified_meetings"] == 24


def test_meetings_list(client):
    body = client.get("/api/meetings").json()
    assert body["count"] == 24
    assert len(body["meetings"]) == 24


@pytest.mark.parametrize("coverage,expected", [
    ("both", 17), ("crm_only", 3), ("calendar_only", 4),
])
def test_coverage_filter(client, coverage, expected):
    body = client.get("/api/meetings", params={"coverage": coverage}).json()
    assert body["count"] == expected


def test_conflict_filter(client):
    body = client.get("/api/meetings", params={"has_conflicts": True}).json()
    assert all(meeting["conflicts"] for meeting in body["meetings"])


def test_severity_filter_narrows_results(client):
    critical = client.get("/api/meetings", params={"min_severity": "critical"}).json()
    low = client.get("/api/meetings", params={"min_severity": "low"}).json()
    assert critical["count"] == 1
    assert low["count"] > critical["count"]


def test_search_matches_a_record_id(client):
    body = client.get("/api/meetings", params={"q": "CRM-1009"}).json()
    assert body["count"] == 1


def test_date_range_filter(client):
    body = client.get("/api/meetings", params={"date_from": "2025-04-01"}).json()
    assert body["count"] >= 1
    assert all(m["start"]["value"] >= "2025-04-01" for m in body["meetings"])


def test_bad_date_is_rejected(client):
    assert client.get("/api/meetings", params={"date_from": "01/04/2025"}).status_code == 400


def test_meeting_detail_includes_raw_source_records(client):
    meeting_id = client.get(
        "/api/meetings", params={"q": "CRM-1002"}
    ).json()["meetings"][0]["id"]

    body = client.get("/api/meetings/" + meeting_id).json()
    assert set(body["raw_records"]) == {"crm", "calendar"}
    assert body["raw_records"]["crm"]["crm_id"] == "CRM-1002"
    assert body["raw_records"]["calendar"]["event_id"] == "CAL-A2"


def test_unknown_meeting_is_404(client):
    assert client.get("/api/meetings/MTG-deadbeef").status_code == 404


def test_conflicts_endpoint_groups_by_field_and_severity(client):
    body = client.get("/api/conflicts").json()
    assert body["count"] == 17
    assert body["by_severity"]["critical"] == 1
    assert "modality" in body["by_field"]


def test_data_quality_exposes_repairs_and_duplicates(client):
    body = client.get("/api/data-quality").json()
    assert body["summary"]["intra_source_duplicates"] == 1
    assert "date_salvaged" in body["warnings_by_code"]


def test_source_record_lookup(client):
    body = client.get("/api/sources/crm/CRM-1008").json()
    assert body["raw"]["meeting_date"] == "03-15/2025"
    assert body["normalized"]["start"].startswith("2025-03-15")


def test_resync_is_idempotent_for_static_files(client):
    before = client.get("/api/stats").json()
    client.post("/api/sync")
    after = client.get("/api/stats").json()
    assert before["unified_meetings"] == after["unified_meetings"]
