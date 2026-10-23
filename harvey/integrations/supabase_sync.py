"""Supabase Cloud Persistence Engine — ensures data/harvey.db is never lost on ephemeral cloud platforms like Render."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import httpx

from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.supabase_sync")

BUCKET_NAME = "harvey-data"
DB_FILE_PATH = PROJECT_ROOT / "data" / "harvey.db"
M365_TOKEN_PATH = PROJECT_ROOT / "data" / "m365_token.json"
DASHBOARD_TOKEN_PATH = PROJECT_ROOT / "data" / "dashboard-token"

SYNC_TIMEOUT = 30.0


def _get_credentials(env=None) -> tuple[str, str]:
    """Retrieve Supabase URL and key from env config or os.environ."""
    url = getattr(env, "supabase_url", "") or os.getenv("SUPABASE_URL", "")
    key = getattr(env, "supabase_service_role_key", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not (url and key):
        try:
            from dotenv import load_dotenv
            load_dotenv()
            url = url or os.getenv("SUPABASE_URL", "")
            key = key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        except Exception:
            pass
    return url.strip().rstrip("/"), key.strip()


async def restore_db_from_supabase(env=None) -> bool:
    """Pull the latest harvey.db and credentials from Supabase Storage on boot."""
    url, key = _get_credentials(env)
    if not url or not key:
        logger.debug("Supabase Sync: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not configured. Skipping restore.")
        return False

    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }

    try:
        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT) as client:
            # 1. Restore SQLite database
            db_url = f"{url}/storage/v1/object/authenticated/{BUCKET_NAME}/harvey.db"
            resp = await client.get(db_url, headers=headers)
            if resp.status_code == 200 and resp.content:
                DB_FILE_PATH.write_bytes(resp.content)
                logger.info(f"Supabase Sync: Restored harvey.db ({len(resp.content)} bytes) from cloud.")
            elif resp.status_code == 404:
                logger.info("Supabase Sync: No existing harvey.db found in cloud bucket; starting fresh.")
            else:
                logger.warning(f"Supabase Sync: DB restore returned status {resp.status_code}")

            # 2. Restore M365 token if present in cloud
            if not M365_TOKEN_PATH.exists():
                token_url = f"{url}/storage/v1/object/authenticated/{BUCKET_NAME}/m365_token.json"
                token_resp = await client.get(token_url, headers=headers)
                if token_resp.status_code == 200 and token_resp.content:
                    M365_TOKEN_PATH.write_bytes(token_resp.content)
                    try:
                        M365_TOKEN_PATH.chmod(0o600)
                    except OSError:
                        pass
                    logger.info("Supabase Sync: Restored m365_token.json from cloud.")

            # 3. Restore dashboard token if present in cloud
            if not DASHBOARD_TOKEN_PATH.exists():
                dash_url = f"{url}/storage/v1/object/authenticated/{BUCKET_NAME}/dashboard-token"
                dash_resp = await client.get(dash_url, headers=headers)
                if dash_resp.status_code == 200 and dash_resp.content:
                    DASHBOARD_TOKEN_PATH.write_bytes(dash_resp.content)
                    try:
                        DASHBOARD_TOKEN_PATH.chmod(0o600)
                    except OSError:
                        pass
                    logger.info("Supabase Sync: Restored dashboard-token from cloud.")

            if DB_FILE_PATH.exists():
                await sync_tables_to_supabase(env)

            return DB_FILE_PATH.exists()
    except Exception as e:
        logger.warning(f"Supabase Sync: Restore failed ({type(e).__name__}): {e}")
        return False


async def backup_db_to_supabase(env=None) -> bool:
    """Push local harvey.db snapshot and credentials to Supabase Storage."""
    url, key = _get_credentials(env)
    if not url or not key:
        return False

    if not DB_FILE_PATH.exists():
        logger.debug("Supabase Sync: local harvey.db does not exist yet. Skipping backup.")
        return False

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "x-upsert": "true",
    }

    try:
        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT) as client:
            # 1. Backup harvey.db
            content = DB_FILE_PATH.read_bytes()
            upload_headers = {**headers, "Content-Type": "application/x-sqlite3"}
            upload_url = f"{url}/storage/v1/object/{BUCKET_NAME}/harvey.db"
            resp = await client.post(upload_url, headers=upload_headers, content=content)
            if resp.status_code not in (200, 201):
                logger.warning(f"Supabase Sync: Backup upload returned status {resp.status_code}: {resp.text}")
                return False

            logger.info(f"Supabase Sync: Successfully backed up harvey.db ({len(content)} bytes) to cloud.")

            # 2. Backup m365_token.json if it exists
            if M365_TOKEN_PATH.exists():
                m365_content = M365_TOKEN_PATH.read_bytes()
                m365_url = f"{url}/storage/v1/object/{BUCKET_NAME}/m365_token.json"
                await client.post(m365_url, headers={**headers, "Content-Type": "application/json"}, content=m365_content)

            # 3. Backup dashboard-token if it exists
            if DASHBOARD_TOKEN_PATH.exists():
                dash_content = DASHBOARD_TOKEN_PATH.read_bytes()
                dash_url = f"{url}/storage/v1/object/{BUCKET_NAME}/dashboard-token"
                await client.post(dash_url, headers={**headers, "Content-Type": "text/plain"}, content=dash_content)

            # 4. Mirror all relational records into Supabase Table Editor
            await sync_tables_to_supabase(env)

            return True
    except Exception as e:
        logger.warning(f"Supabase Sync: Backup failed ({type(e).__name__}): {e}")
        return False


MIRRORED_TABLES = (
    "signal_codes",
    "companies",
    "prospects",
    "campaigns",
    "outbox",
    "conversations",
    "observations",
    "runs",
    "usage_events",
    "settings",
)


def _prepare_row_for_supabase(row: dict) -> dict:
    """Ensure JSON fields are parsed so PostgreSQL JSONB receives native arrays/objects."""
    import json
    out = {}
    for k, v in row.items():
        if k.endswith("_json") and isinstance(v, str) and v.startswith(("[", "{")):
            try:
                out[k] = json.loads(v)
            except Exception:
                out[k] = v
        else:
            out[k] = v
    return out


async def sync_tables_to_supabase(env=None) -> bool:
    """Push SQLite rows into Supabase PostgreSQL tables so they populate the Table Editor."""
    import sqlite3
    url, key = _get_credentials(env)
    if not url or not key:
        logger.debug("Supabase Sync: Credentials not found. Skipping table sync.")
        return False

    if not DB_FILE_PATH.exists():
        logger.debug("Supabase Sync: local harvey.db does not exist. Skipping table sync.")
        return False

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    try:
        con = sqlite3.connect(str(DB_FILE_PATH))
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT) as client:
            total_synced = 0
            for table in MIRRORED_TABLES:
                try:
                    cur.execute(f"SELECT * FROM {table}")
                    raw_rows = cur.fetchall()
                except sqlite3.OperationalError:
                    # Table might not exist yet in local SQLite
                    continue

                if not raw_rows:
                    continue

                rows = [_prepare_row_for_supabase(dict(r)) for r in raw_rows]
                # Batch in chunks of 100
                chunk_size = 100
                for i in range(0, len(rows), chunk_size):
                    chunk = rows[i:i + chunk_size]
                    post_url = f"{url}/rest/v1/{table}"
                    resp = await client.post(post_url, headers=headers, json=chunk)
                    if resp.status_code in (200, 201):
                        total_synced += len(chunk)
                    else:
                        logger.warning(
                            f"Supabase Sync: Table '{table}' batch upsert failed: {resp.status_code} {resp.text}"
                        )

            logger.info(f"Supabase Sync: Mirrored {total_synced} rows across tables to Supabase Table Editor.")
            return True
    except Exception as e:
        logger.warning(f"Supabase Sync: Table sync failed ({type(e).__name__}): {e}")
        return False

