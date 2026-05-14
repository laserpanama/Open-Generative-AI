import os
import sqlite3
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 3600))
API_URL = os.getenv(
    "API_URL", "https://ocds.panamacompra.gob.pa/api/v1/releases"
)
DB_PATH = os.getenv("DB_PATH", "panamacompra_tenders.db")

GEO_KEYWORDS = ["BELLA VISTA", "EL CANGREJO", "SAN FRANCISCO", "MARBELLA"]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tenders (
            ocid        TEXT PRIMARY KEY,
            title       TEXT,
            entity      TEXT,
            amount      REAL,
            attempts    INTEGER DEFAULT 1,
            last_seen   TEXT,
            first_seen  TEXT
        )
        """
    )
    conn.commit()


def upsert_tender(
    conn: sqlite3.Connection,
    ocid: str,
    title: str,
    entity: str,
    amount: float,
) -> int:
    """Insert or increment attempt count. Returns the new attempt number."""
    now = datetime.utcnow().isoformat()
    row = conn.execute(
        "SELECT attempts FROM tenders WHERE ocid = ?", (ocid,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO tenders (ocid, title, entity, amount, attempts, last_seen, first_seen) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (ocid, title, entity, amount, now, now),
        )
        conn.commit()
        return 1

    new_attempts = row[0] + 1
    conn.execute(
        "UPDATE tenders SET attempts = ?, last_seen = ?, title = ?, entity = ?, amount = ? "
        "WHERE ocid = ?",
        (new_attempts, now, title, entity, amount, ocid),
    )
    conn.commit()
    return new_attempts


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN:
        print(f"[ALERT — no token] {message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[WARN] Telegram alert failed: {exc}")


def build_alert(attempt: int, ocid: str, entity: str, title: str, amount: float) -> str:
    geo_flag = ""
    title_upper = title.upper()
    entity_upper = entity.upper()
    for kw in GEO_KEYWORDS:
        if kw in title_upper or kw in entity_upper:
            geo_flag = f"\n📍 *Geographic Flag:* {kw}"
            break

    label = "3rd ATTEMPT — PROCEDIMIENTO EXCEPCIONAL RISK" if attempt >= 3 else f"Attempt #{attempt}"
    return (
        f"🚨 *PANAMACOMPRA ALERT*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *{label}*\n"
        f"🏢 *Entity:* {entity}\n"
        f"📋 *Project:* {title}\n"
        f"💰 *Value:* B/. {amount:,.2f}\n"
        f"🔗 OCID: `{ocid}`"
        f"{geo_flag}"
    )


def trigger_alert(attempt: int, ocid: str, entity: str, title: str, amount: float) -> None:
    print(
        f"[{datetime.utcnow().isoformat()}] ALERT attempt={attempt} ocid={ocid} entity={entity!r}"
    )
    send_telegram(build_alert(attempt, ocid, entity, title, amount))


# ---------------------------------------------------------------------------
# API polling
# ---------------------------------------------------------------------------

def fetch_releases(page: int = 1, page_size: int = 100) -> list[dict]:
    params = {"page": page, "pageSize": page_size}
    try:
        resp = requests.get(API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("releases", data if isinstance(data, list) else [])
    except Exception as exc:
        print(f"[WARN] API fetch failed: {exc}")
        return []


def extract_fields(release: dict) -> tuple[str, str, str, float]:
    ocid = release.get("ocid", "")
    tender = release.get("tender", {})
    title = tender.get("title", release.get("id", "Unknown"))
    parties = release.get("parties", [])
    entity = next(
        (p.get("name", "") for p in parties if "buyer" in p.get("roles", [])),
        release.get("buyer", {}).get("name", "Unknown"),
    )
    amount = 0.0
    try:
        amount = float(
            tender.get("value", {}).get("amount", 0)
            or release.get("contracts", [{}])[0].get("value", {}).get("amount", 0)
        )
    except (TypeError, IndexError, ValueError):
        pass
    return ocid, title, entity, amount


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def poll_once(conn: sqlite3.Connection) -> None:
    print(f"[{datetime.utcnow().isoformat()}] Polling {API_URL}")
    releases = fetch_releases()
    print(f"  → {len(releases)} releases received")

    for release in releases:
        ocid, title, entity, amount = extract_fields(release)
        if not ocid:
            continue

        attempt = upsert_tender(conn, ocid, title, entity, amount)

        if attempt >= 3:
            trigger_alert(attempt, ocid, entity, title, amount)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    print(f"PanamaCompra Watchdog started. DB={DB_PATH} interval={POLL_INTERVAL}s")

    try:
        while True:
            poll_once(conn)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("Watchdog stopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
