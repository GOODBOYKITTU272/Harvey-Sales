"""Unit tests for Supabase cloud persistence sync module."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from harvey.config import EnvConfig
from harvey.integrations import supabase_sync


@pytest.mark.asyncio
async def test_restore_skips_when_no_credentials(monkeypatch):
    monkeypatch.setattr(supabase_sync, "_get_credentials", lambda env=None: ("", ""))
    res = await supabase_sync.restore_db_from_supabase()
    assert res is False


@pytest.mark.asyncio
async def test_backup_skips_when_no_credentials(monkeypatch):
    monkeypatch.setattr(supabase_sync, "_get_credentials", lambda env=None: ("", ""))
    res = await supabase_sync.backup_db_to_supabase()
    assert res is False


@pytest.mark.asyncio
async def test_backup_skips_when_no_db(monkeypatch, tmp_path):
    monkeypatch.setattr(supabase_sync, "_get_credentials", lambda env=None: ("https://test.supabase.co", "key123"))
    monkeypatch.setattr(supabase_sync, "DB_FILE_PATH", tmp_path / "data" / "nonexistent.db")
    res = await supabase_sync.backup_db_to_supabase()
    assert res is False


@pytest.mark.asyncio
async def test_restore_downloads_db_and_tokens(monkeypatch, tmp_path):
    db_file = tmp_path / "data" / "harvey.db"
    m365_file = tmp_path / "data" / "m365_token.json"
    dash_file = tmp_path / "data" / "dashboard-token"

    monkeypatch.setattr(supabase_sync, "_get_credentials", lambda env=None: ("https://test.supabase.co", "key123"))
    monkeypatch.setattr(supabase_sync, "DB_FILE_PATH", db_file)
    monkeypatch.setattr(supabase_sync, "M365_TOKEN_PATH", m365_file)
    monkeypatch.setattr(supabase_sync, "DASHBOARD_TOKEN_PATH", dash_file)
    monkeypatch.setattr(supabase_sync, "PROJECT_ROOT", tmp_path)

    async def fake_get(url, headers):
        req = httpx.Request("GET", url)
        if "harvey.db" in url:
            return httpx.Response(200, content=b"fake-sqlite-content", request=req)
        if "m365_token.json" in url:
            return httpx.Response(200, content=b'{"token": "test"}', request=req)
        if "dashboard-token" in url:
            return httpx.Response(200, content=b"ediscas2026", request=req)
        return httpx.Response(404, request=req)

    with patch("httpx.AsyncClient.get", side_effect=fake_get):
        res = await supabase_sync.restore_db_from_supabase()
        assert res is True
        assert db_file.exists()
        assert db_file.read_bytes() == b"fake-sqlite-content"
        assert m365_file.exists()
        assert m365_file.read_bytes() == b'{"token": "test"}'
        assert dash_file.exists()
        assert dash_file.read_bytes() == b"ediscas2026"


@pytest.mark.asyncio
async def test_backup_uploads_db_and_tokens(monkeypatch, tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    db_file = tmp_path / "data" / "harvey.db"
    db_file.write_bytes(b"live-db-bytes")
    m365_file = tmp_path / "data" / "m365_token.json"
    m365_file.write_bytes(b'{"live": "token"}')

    monkeypatch.setattr(supabase_sync, "_get_credentials", lambda env=None: ("https://test.supabase.co", "key123"))
    monkeypatch.setattr(supabase_sync, "DB_FILE_PATH", db_file)
    monkeypatch.setattr(supabase_sync, "M365_TOKEN_PATH", m365_file)
    monkeypatch.setattr(supabase_sync, "DASHBOARD_TOKEN_PATH", tmp_path / "data" / "nonexistent-dash")

    uploaded_urls = []

    async def fake_post(url, headers, content=None):
        uploaded_urls.append(url)
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"Key": "uploaded"}, request=req)

    with patch("httpx.AsyncClient.post", side_effect=fake_post):
        res = await supabase_sync.backup_db_to_supabase()
        assert res is True
        assert any("harvey.db" in u for u in uploaded_urls)
        assert any("m365_token.json" in u for u in uploaded_urls)


def test_prepare_row_for_supabase_parses_json():
    row = {
        "id": "c1",
        "name": "Acme Corp",
        "tech_stack_json": '["Python", "React"]',
        "signals_json": '{"hiring": true}',
        "notes": "not json",
    }
    prepared = supabase_sync._prepare_row_for_supabase(row)
    assert prepared["id"] == "c1"
    assert prepared["tech_stack_json"] == ["Python", "React"]
    assert prepared["signals_json"] == {"hiring": True}
    assert prepared["notes"] == "not json"


@pytest.mark.asyncio
async def test_sync_tables_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(supabase_sync, "_get_credentials", lambda env=None: ("", ""))
    res = await supabase_sync.sync_tables_to_supabase()
    assert res is False


@pytest.mark.asyncio
async def test_sync_tables_skips_without_db(monkeypatch, tmp_path):
    monkeypatch.setattr(supabase_sync, "_get_credentials", lambda env=None: ("https://test.supabase.co", "key123"))
    monkeypatch.setattr(supabase_sync, "DB_FILE_PATH", tmp_path / "data" / "missing.db")
    res = await supabase_sync.sync_tables_to_supabase()
    assert res is False


@pytest.mark.asyncio
async def test_sync_tables_success_with_mock(monkeypatch, tmp_path):
    import sqlite3
    db_file = tmp_path / "data" / "harvey.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_file))
    con.execute("CREATE TABLE signal_codes (code TEXT PRIMARY KEY, label TEXT)")
    con.execute("INSERT INTO signal_codes VALUES ('test_code', 'Test Label')")
    con.commit()
    con.close()

    monkeypatch.setattr(supabase_sync, "_get_credentials", lambda env=None: ("https://test.supabase.co", "key123"))
    monkeypatch.setattr(supabase_sync, "DB_FILE_PATH", db_file)

    posts = []

    async def fake_post(url, headers, json=None):
        posts.append((url, json))
        req = httpx.Request("POST", url)
        return httpx.Response(201, json={"status": "ok"}, request=req)

    with patch("httpx.AsyncClient.post", side_effect=fake_post):
        res = await supabase_sync.sync_tables_to_supabase()
        assert res is True
        assert len(posts) >= 1
        assert any("signal_codes" in p[0] for p in posts)
        synced_item = next(p[1] for p in posts if "signal_codes" in p[0])[0]
        assert synced_item["code"] == "test_code"

