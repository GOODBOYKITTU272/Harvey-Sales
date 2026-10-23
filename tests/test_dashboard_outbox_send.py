"""Tests for POST /api/outbox/send endpoint."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey.config import HarveyConfig, EnvConfig
from harvey.models import Company
from harvey.models.campaign import Campaign
from harvey.models.prospect import Prospect
from harvey.state import StateManager


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "harvey.db"
        monkeypatch.setattr(dash, "DB_PATH", db)
        monkeypatch.setattr(dash, "WEB_DIR", Path(__file__).resolve().parent.parent / "harvey" / "web")
        dash.token_store.configure(Path(tmp) / "data" / "dashboard-token")
        token = dash.token_store.get()
        with TestClient(dash.app, base_url="http://127.0.0.1:5555",
                        headers={"Authorization": f"Bearer {token}"}) as c:
            c.db_path = db
            yield c


@pytest.mark.asyncio
async def test_outbox_send_requires_auth(client):
    unauth = TestClient(dash.app, base_url="http://127.0.0.1:5555")
    res = unauth.post("/api/outbox/send")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_outbox_send_when_paused(client):
    state = StateManager(str(client.db_path))
    await state.init_db()
    await state.set_setting("sending_paused", "testing pause")

    res = client.post("/api/outbox/send")
    assert res.status_code == 400
    assert "paused" in res.json()["message"]


@pytest.mark.asyncio
async def test_outbox_send_drains_approved(client, monkeypatch):
    state = StateManager(str(client.db_path))
    await state.init_db()

    # Seed company, prospect, and approved outbox item
    cid = await state.add_company(Company(name="Test Co", domain="test.example"))
    pid = await state.add_prospect(Prospect(
        email="lead@test.example", email_status="verified", status="new",
        company="Test Co", company_id=cid,
    ))
    camp_id = await state.add_campaign(Campaign(
        id="", name="test-camp", channel="email", sequence=[], prospect_ids=[pid], status="active",
    ))
    await state.add_outbox_item(
        prospect_id=pid,
        campaign_id=camp_id,
        step=1,
        to_email="lead@test.example",
        subject="Hello",
        body="World",
        status="approved",
        send_at="2020-01-01T00:00:00",
    )

    # Mock Sender and Handler
    fake_provider = MagicMock()
    fake_provider.name = "microsoft"
    fake_provider.is_configured.return_value = True

    base_cfg = {
        "persona": {"name": "H", "company": "A", "role": "B", "email": "e@test.example", "linkedin": "", "tone": "t"},
        "product": {"name": "P", "description": "D", "pricing": "$1", "key_benefits": [], "objection_responses": {}},
        "icp": {"industries": [], "company_size": "", "titles": [], "geography": []},
        "channels": {"email": {"enabled": True, "provider": "microsoft", "max_daily_sends": 50}},
    }
    monkeypatch.setattr("harvey.config.load_config", lambda: HarveyConfig(**base_cfg))
    monkeypatch.setattr("harvey.config.load_env", lambda: EnvConfig())

    from harvey.integrations.mail_provider import SendResult

    mock_send = AsyncMock(return_value=SendResult(ok=True, message_id="msg123"))
    fake_provider.send_email = mock_send
    fake_provider.get_replies = AsyncMock(return_value=[])

    monkeypatch.setattr("harvey.agents.sender.get_mail_provider", lambda c, e: fake_provider)
    monkeypatch.setattr("harvey.agents.handler.get_mail_provider", lambda c, e: fake_provider)
    monkeypatch.setattr("harvey.integrations.email_finder.get_mx_host", AsyncMock(return_value="mail.test.example"))

    res = client.post("/api/outbox/send")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["sent"] == 1

    # Verify outbox item transitioned to sent
    outbox = await state.get_outbox(status="sent")
    assert len(outbox) == 1
    assert outbox[0]["to_email"] == "lead@test.example"


@pytest.mark.asyncio
async def test_outbox_send_blocks_nonexistent_domain(client, monkeypatch):
    state = StateManager(str(client.db_path))
    await state.init_db()

    cid = await state.add_company(Company(name="Dead Co", domain="dead.invalid"))
    pid = await state.add_prospect(Prospect(
        email="lead@dead.invalid", email_status="verified", status="new",
        company="Dead Co", company_id=cid,
    ))
    camp_id = await state.add_campaign(Campaign(
        id="", name="test-camp-2", channel="email", sequence=[], prospect_ids=[pid], status="active",
    ))
    await state.add_outbox_item(
        prospect_id=pid, campaign_id=camp_id, step=1,
        to_email="lead@dead.invalid", subject="Hello", body="World",
        status="approved", send_at="2020-01-01T00:00:00",
    )

    fake_provider = MagicMock()
    fake_provider.name = "microsoft"
    fake_provider.is_configured.return_value = True
    fake_provider.send_email = AsyncMock()

    base_cfg = {
        "persona": {"name": "H", "company": "A", "role": "B", "email": "e@test.example", "linkedin": "", "tone": "t"},
        "product": {"name": "P", "description": "D", "pricing": "$1", "key_benefits": [], "objection_responses": {}},
        "icp": {"industries": [], "company_size": "", "titles": [], "geography": []},
        "channels": {"email": {"enabled": True, "provider": "microsoft", "max_daily_sends": 50}},
    }
    monkeypatch.setattr("harvey.config.load_config", lambda: HarveyConfig(**base_cfg))
    monkeypatch.setattr("harvey.config.load_env", lambda: EnvConfig())
    monkeypatch.setattr("harvey.agents.sender.get_mail_provider", lambda c, e: fake_provider)
    monkeypatch.setattr("harvey.integrations.email_finder.get_mx_host", AsyncMock(return_value=None))

    res = client.post("/api/outbox/send")
    assert res.status_code == 200
    assert res.json()["sent"] == 0
    fake_provider.send_email.assert_not_called()

    failed = await state.get_outbox(status="failed")
    assert len(failed) == 1
    assert "no valid MX mail servers" in failed[0]["error"]
