"""Tests for POST /api/pipeline/seed endpoint."""

import asyncio
import pytest
from tests.test_dashboard_signals import client  # noqa: F401 (fixture)
from harvey.state import StateManager


def test_seed_pipeline_populates_facilities_and_outbox(client):
    """Calling /api/pipeline/seed on an empty database populates EDISCAS accounts and drafts."""
    db = client.db_path
    asyncio.run(StateManager(str(db)).init_db())

    # Initially empty
    today_before = client.get("/api/today").json()
    assert (today_before.get("stats") or {}).get("prospects", 0) == 0

    # Trigger seeding
    res = client.post("/api/pipeline/seed", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["seeded"] is True
    assert data["companies"] == 8
    assert data["prospects"] == 8
    assert data["outbox_pending"] == 8

    # Verify Today stats reflect the new records
    today_after = client.get("/api/today").json()
    stats = today_after.get("stats") or {}
    assert stats.get("prospects") == 8
    assert stats.get("companies") == 8
    assert stats.get("outbox_pending") == 8

    # Verify outbox items are in pending_review
    outbox_res = client.get("/api/outbox")
    assert outbox_res.status_code == 200
    outbox_payload = outbox_res.json()
    outbox_items = outbox_payload.get("pending", [])
    assert len(outbox_items) == 8
    assert all(item["status"] == "pending_review" for item in outbox_items)
    assert any("Al Quoz" in item["subject"] for item in outbox_items)
    assert any("Houston Port" in item["subject"] for item in outbox_items)

    # Calling again without force is idempotent
    second_res = client.post("/api/pipeline/seed", json={})
    assert second_res.status_code == 200
    second_data = second_res.json()
    assert second_data["success"] is True
    assert second_data["seeded"] is False
    assert second_data["prospects"] == 8


def test_seed_pipeline_requires_auth():
    """Unauthenticated call to /api/pipeline/seed is rejected by DashboardGuard."""
    from fastapi.testclient import TestClient
    import harvey.dashboard as dash

    with TestClient(dash.app, base_url="http://127.0.0.1:5555") as unauthed:
        res = unauthed.post("/api/pipeline/seed", json={})
        assert res.status_code == 401
