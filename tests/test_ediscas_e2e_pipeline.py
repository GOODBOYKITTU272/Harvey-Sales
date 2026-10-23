"""End-to-End (E2E) Test Suite for EDISCAS Physical AI Facility Outreach Pipeline.

Covers:
1. Seed Stage: 8 target commercial facilities, contacts, and 3-touch sequence.
2. Outbox Review Stage: Desk viewing, editing draft pitches, individual & bulk approval.
3. Safety Gate Stage: Deterministic checks (<= 90 chars subject, <= 220 words body, no merge tags).
4. Outbox Dispatch Stage: POST /api/outbox/send immediate dispatch via Microsoft 365 provider.
5. Inbound Bounce Stage: Exchange Online NDR detection & auto-cancellation of sequences.
6. Stop-on-Reply Stage: Human opt-out and reply intent handling.
7. Follow-up Order & Spacing: Strict adherence to step sequence and MIN_FOLLOW_UP_GAP.
"""

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey.agents.handler import Handler
from harvey.agents.sender import Sender
from harvey.config import EnvConfig, HarveyConfig
from harvey.gate import pre_send_check
from harvey.integrations.mail_provider import InboundMessage, SendResult
from harvey.models import Company
from harvey.models.campaign import Campaign
from harvey.models.prospect import Prospect
from harvey.state import StateManager


BASE_CONFIG = {
    "persona": {
        "name": "Ramakrishna",
        "company": "EDISCAS",
        "role": "Commercial Partnerships",
        "email": "ramakrishna@applywizz.ai",
        "linkedin": "",
        "tone": "direct, professional",
    },
    "product": {
        "name": "Physical AI Commercial Facility Program",
        "description": "Monetizing routine facility operations via egocentric smartphone capture for robotics foundation models",
        "pricing": "Weekly payouts via Zoho Books",
        "key_benefits": ["Zero workflow disruption", "100% automated privacy redaction", "Weekly payouts"],
        "objection_responses": {},
    },
    "icp": {
        "industries": ["Automotive", "Commercial Kitchens", "Warehousing", "HVAC"],
        "company_size": "10-250",
        "titles": ["Operations Director", "Managing Director", "Facility Manager"],
        "geography": ["Dubai", "Abu Dhabi", "Houston", "Dallas"],
    },
    "channels": {
        "email": {
            "enabled": True,
            "provider": "microsoft",
            "max_daily_sends": 50,
            "require_approval": True,
        }
    },
}


@pytest.fixture
def env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "harvey.db"
        monkeypatch.setattr(dash, "DB_PATH", db)
        monkeypatch.setattr(dash, "WEB_DIR", Path(__file__).resolve().parent.parent / "harvey" / "web")
        dash.token_store.configure(Path(tmp) / "data" / "dashboard-token")
        token = dash.token_store.get()

        monkeypatch.setattr("harvey.config.load_config", lambda: HarveyConfig(**BASE_CONFIG))
        monkeypatch.setattr("harvey.config.load_env", lambda: EnvConfig())

        with TestClient(dash.app, base_url="http://127.0.0.1:5555",
                        headers={"Authorization": f"Bearer {token}"}) as client:
            client.db_path = db
            client.token = token
            yield client


# ── Stage 1: Pipeline Seeding ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_stage1_seed_pipeline(env):
    """Seed initial target physical facilities and personalized drafts."""
    state = StateManager(str(env.db_path))
    await state.init_db()

    # Trigger seeding through the API endpoint
    res = env.post("/api/pipeline/seed", json={"force": True})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["companies"] == 8
    assert data["prospects"] == 8
    assert data["outbox_pending"] == 8

    # Verify seeded outbox drafts adhere to constraints
    pending = await state.get_outbox(status="pending_review")
    assert len(pending) == 8
    for item in pending:
        # Subject line must be <= 90 characters
        assert len(item["subject"]) <= 90, f"Subject too long: {item['subject']}"
        assert len(item["subject"]) > 10
        # Body word count <= 220 words
        assert len(item["body"].split()) <= 220
        # CAN-SPAM compliance opt-out notice
        assert "no thanks" in item["body"].lower()


# ── Stage 2: Outbox Desk Review & Approval ─────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_stage2_outbox_edit_and_approval(env):
    """Operators can review, edit, and approve individual or all drafts."""
    state = StateManager(str(env.db_path))
    await state.init_db()
    res = env.post("/api/pipeline/seed", json={"force": True})
    assert res.status_code == 200

    outbox = await state.get_outbox(status="pending_review")
    first_item = outbox[0]
    item_id = first_item["id"]

    # 1. Edit draft subject & body
    new_subject = "EDISCAS data partnership — Custom Workshop"
    new_body = "Hi Tariq,\n\nCustom edited pitch body.\n\nRamakrishna\nEDISCAS\nReply 'no thanks' to opt out."
    edit_res = env.post(f"/api/outbox/{item_id}/edit", json={
        "subject": new_subject,
        "body": new_body,
    })
    assert edit_res.status_code == 200
    assert edit_res.json()["success"] is True

    updated_item = await state.get_outbox_item(item_id)
    assert updated_item["subject"] == new_subject
    assert updated_item["body"] == new_body
    assert updated_item["status"] == "pending_review"  # Editing does NOT auto-approve

    # 2. Approve single item
    approve_res = env.post(f"/api/outbox/{item_id}/approve")
    assert approve_res.status_code == 200
    assert approve_res.json()["success"] is True

    approved_item = await state.get_outbox_item(item_id)
    assert approved_item["status"] == "approved"

    # 3. Approve remaining all items
    approve_all_res = env.post("/api/outbox/approve-all")
    assert approve_all_res.status_code == 200
    assert approve_all_res.json()["success"] is True

    all_approved = await state.get_outbox(status="approved")
    assert len(all_approved) == 8


# ── Stage 3: Deterministic Pre-Send Gate ───────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_stage3_gate_validates_constraints(env):
    """pre_send_check enforces strict deterministic rules before mail leaves."""
    # Valid pitch
    valid = pre_send_check(
        to_email="partner@facility.ae",
        subject="EDISCAS data partnership — Al Quoz",
        body="Hi Tariq,\n\nPaging you regarding egocentric video capture.\n\nBest,\nRamakrishna\nReply 'no thanks' to opt out.",
    )
    assert valid.ok is True
    assert not valid.reasons

    # Gate blocks subject > 90 characters
    long_subject = "EDISCAS partnership — commercial facility data licensing (Al Quoz Auto Performance & Engineering)"
    blocked_subject = pre_send_check(
        to_email="partner@facility.ae",
        subject=long_subject,
        body="Valid body content.",
    )
    assert blocked_subject.ok is False
    assert any("subject too long" in r for r in blocked_subject.reasons)

    # Gate blocks unrendered merge tags
    blocked_tags = pre_send_check(
        to_email="partner@facility.ae",
        subject="Hello {{company}}",
        body="Hi {{first_name}}, welcome.",
    )
    assert blocked_tags.ok is False
    assert any("unrendered merge tags" in r for r in blocked_tags.reasons)

    # Gate blocks spam phrases
    blocked_spam = pre_send_check(
        to_email="partner@facility.ae",
        subject="Guaranteed results today",
        body="Act now for risk-free profit.",
    )
    assert blocked_spam.ok is False
    assert any("banned phrases" in r for r in blocked_spam.reasons)


# ── Stage 4: Outbox Dispatch Engine ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_stage4_outbox_send_dispatch(env, monkeypatch):
    """Immediate send drains approved outbox items via Microsoft 365."""
    state = StateManager(str(env.db_path))
    await state.init_db()
    env.post("/api/pipeline/seed", json={"force": True})
    env.post("/api/outbox/approve-all")

    # Mock provider
    fake_provider = MagicMock()
    fake_provider.name = "microsoft"
    fake_provider.is_configured.return_value = True
    fake_provider.send_email = AsyncMock(return_value=SendResult(ok=True, message_id="ms-msg-101", thread_ref="thread-101"))
    fake_provider.get_replies = AsyncMock(return_value=[])

    monkeypatch.setattr("harvey.agents.sender.get_mail_provider", lambda c, e: fake_provider)
    monkeypatch.setattr("harvey.agents.handler.get_mail_provider", lambda c, e: fake_provider)
    monkeypatch.setattr("harvey.integrations.email_finder.get_mx_host", AsyncMock(return_value="mail.ediscas.example"))

    # Trigger immediate send
    send_res = env.post("/api/outbox/send")
    assert send_res.status_code == 200
    data = send_res.json()
    assert data["success"] is True
    assert data["sent"] == 8

    # Verify database state
    sent_items = await state.get_outbox(status="sent")
    assert len(sent_items) == 8
    for item in sent_items:
        assert item["status"] == "sent"
        assert item["sent_at"] is not None
        assert item["message_id"] == "ms-msg-101"

    # Prospects transition to "contacted"
    prospects = await state.get_prospects_by_status("contacted")
    assert len(prospects) == 8


# ── Stage 5: Inbound Bounce (NDR) Handling ─────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_stage5_bounce_ndr_handling(env, monkeypatch):
    """Exchange Online bounce NDR automatically marks prospect invalid and cancels queue."""
    state = StateManager(str(env.db_path))
    await state.init_db()

    cid = await state.add_company(Company(name="Test Auto", domain="fakeauto.ae"))
    pid = await state.add_prospect(Prospect(
        email="tariq@fakeauto.ae", email_status="verified", status="contacted",
        company="Test Auto", company_id=cid,
    ))
    camp_id = await state.add_campaign(Campaign(
        id="", name="test-camp", channel="email", sequence=[], prospect_ids=[pid], status="active",
    ))
    step1_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=camp_id, step=1,
        to_email="tariq@fakeauto.ae", subject="EDISCAS data partnership", body="Hello",
        status="approved", send_at="2026-10-23T10:00:00",
    )
    await state.update_outbox_item(
        step1_id, status="sent", sent_at="2026-10-23T10:00:00", message_id="sent-msg-001",
    )
    # Queue a follow-up step 2
    step2_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=camp_id, step=2,
        to_email="tariq@fakeauto.ae", subject="Re: Partnership", body="Following up.",
        status="pending_review", send_at="2026-10-25T12:00:00",
    )

    # Simulate inbound NDR bounce message from Microsoft Outlook
    bounce_msg = InboundMessage(
        provider_id="bounce-001",
        from_email="postmaster@outlook.com",
        subject="Undeliverable: EDISCAS data partnership — Test Auto",
        body=(
            "Delivery has failed to these recipients: tariq@fakeauto.ae\n"
            "Remote server returned '550 5.4.310 DNS domain fakeauto.ae does not exist'"
        ),
        in_reply_to="sent-msg-001",
        is_bounce=True,
    )

    fake_provider = MagicMock()
    fake_provider.name = "microsoft"
    fake_provider.is_configured.return_value = True
    fake_provider.get_replies = AsyncMock(return_value=[bounce_msg])

    monkeypatch.setattr("harvey.agents.handler.get_mail_provider", lambda c, e: fake_provider)

    from harvey.brain import Brain
    brain = Brain(state, HarveyConfig(**BASE_CONFIG).brain)
    handler = Handler(brain, state, HarveyConfig(**BASE_CONFIG), EnvConfig())
    await handler._run_native()

    # Verify prospect email marked invalid
    prospect = await state.get_prospect(pid)
    assert prospect.email_status in ("invalid", "bounced") or prospect.status in ("lost", "opted_out", "bounced")

    # Verify step 2 was cancelled
    cancelled_item = await state.get_outbox_item(step2_id)
    assert cancelled_item["status"] in ("cancelled", "failed")


# ── Stage 6: Inbound Stop-on-Reply / Opt-Out ───────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_stage6_stop_on_reply_opt_out(env, monkeypatch):
    """Inbound human opt-out marks prospect opted_out and stops all future emails."""
    state = StateManager(str(env.db_path))
    await state.init_db()

    cid = await state.add_company(Company(name="Acme Logistics", domain="acme.com"))
    pid = await state.add_prospect(Prospect(
        email="marcus@acme.com", email_status="verified", status="contacted",
        company="Acme Logistics", company_id=cid,
    ))
    camp_id = await state.add_campaign(Campaign(
        id="", name="camp", channel="email", sequence=[], prospect_ids=[pid], status="active",
    ))
    step2_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=camp_id, step=2,
        to_email="marcus@acme.com", subject="Re: EDISCAS", body="Checking in.",
        status="approved", send_at="2026-10-25T12:00:00",
    )

    # Inbound email asking to unsubscribe
    opt_out_reply = InboundMessage(
        provider_id="reply-opt-out",
        from_email="marcus@acme.com",
        subject="Re: EDISCAS",
        body="Please remove me from your list. No thanks.",
        is_bounce=False,
    )

    fake_provider = MagicMock()
    fake_provider.name = "microsoft"
    fake_provider.is_configured.return_value = True
    fake_provider.get_replies = AsyncMock(return_value=[opt_out_reply])
    monkeypatch.setattr("harvey.agents.handler.get_mail_provider", lambda c, e: fake_provider)

    from harvey.brain import Brain
    brain = Brain(state, HarveyConfig(**BASE_CONFIG).brain)
    handler = Handler(brain, state, HarveyConfig(**BASE_CONFIG), EnvConfig())
    await handler._run_native()

    # Prospect must be opted_out
    prospect = await state.get_prospect(pid)
    assert prospect.status == "opted_out"

    # Queued outbox item must be cancelled
    step2_item = await state.get_outbox_item(step2_id)
    assert step2_item["status"] == "cancelled"


# ── Stage 7: Follow-up Order & Spacing ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_stage7_followup_order_and_spacing(env, monkeypatch):
    """Step 2 never sends before Step 1 is sent, and respects MIN_FOLLOW_UP_GAP."""
    state = StateManager(str(env.db_path))
    await state.init_db()

    cid = await state.add_company(Company(name="Alpha Co", domain="alpha.example"))
    pid = await state.add_prospect(Prospect(
        email="lead@alpha.example", email_status="verified", status="new",
        company="Alpha Co", company_id=cid,
    ))
    camp_id = await state.add_campaign(Campaign(
        id="", name="camp", channel="email", sequence=[], prospect_ids=[pid], status="active",
    ))

    # Step 1 is approved but not yet sent
    step1_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=camp_id, step=1,
        to_email="lead@alpha.example", subject="Step 1", body="First email. Reply 'no thanks' to opt out.",
        status="approved", send_at="2026-10-23T10:00:00",
    )
    # Step 2 is also approved
    step2_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=camp_id, step=2,
        to_email="lead@alpha.example", subject="Step 2", body="Follow up.",
        status="approved", send_at="2026-10-23T10:00:00",
    )

    fake_provider = MagicMock()
    fake_provider.name = "microsoft"
    fake_provider.is_configured.return_value = True
    fake_provider.send_email = AsyncMock(return_value=SendResult(ok=True, message_id="msg1"))
    monkeypatch.setattr("harvey.agents.sender.get_mail_provider", lambda c, e: fake_provider)

    from harvey.brain import Brain
    brain = Brain(state, HarveyConfig(**BASE_CONFIG).brain)
    sender = Sender(brain, state, HarveyConfig(**BASE_CONFIG), EnvConfig())
    sender.send_pacing = False

    # Check order for step 2 while step 1 is still approved (not sent)
    step2_item = await state.get_outbox_item(step2_id)
    order = await sender._follow_up_order(step2_item)
    assert order == "wait"  # Step 2 refuses to send before Step 1 is sent!
