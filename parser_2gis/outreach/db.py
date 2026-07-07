from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ..paths import user_path

# Schema version bumped when the DDL below changes in a breaking way.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    phone        TEXT,
    phone_wa     TEXT,                       -- digits only, ready for a WA jid
    city         TEXT,
    niche        TEXT,                       -- rubric label the search was run under
    rubric       TEXT,                       -- rubric code, if known
    address      TEXT,
    has_whatsapp INTEGER NOT NULL DEFAULT 0,
    source_url   TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE(phone_wa, niche)
);

CREATE TABLE IF NOT EXISTS sites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    niche      TEXT NOT NULL,
    city       TEXT,
    slug       TEXT NOT NULL UNIQUE,
    url        TEXT,
    status     TEXT NOT NULL DEFAULT 'built',  -- built | deployed | failed
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id          INTEGER REFERENCES sites(id),
    niche            TEXT NOT NULL,
    city             TEXT,
    message_template TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'draft',  -- draft | running | paused | done
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued | sent | delivered | failed
    error       TEXT,
    sent_at     TEXT,
    UNIQUE(campaign_id, lead_id)
);

CREATE INDEX IF NOT EXISTS idx_leads_niche   ON leads(niche);
CREATE INDEX IF NOT EXISTS idx_messages_camp ON messages(campaign_id);
"""


def default_db_path() -> Path:
    """Location of the outreach SQLite database in the user's data directory."""
    return user_path(is_config=False) / 'outreach.db'


def _now() -> str:
    """UTC timestamp in ISO-8601 for storage."""
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (creating if needed) the outreach database and ensure the schema.

    Rows are returned as `sqlite3.Row` so callers can use column names.
    """
    if db_path is None:
        db_path = default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(_SCHEMA)
    conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    conn.commit()
    return conn


@contextmanager
def session(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a connection, committing on success."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- leads ----------------------------------------------------------------

def upsert_lead(conn: sqlite3.Connection, *, name: str, phone: Optional[str],
                phone_wa: Optional[str], city: Optional[str], niche: Optional[str],
                rubric: Optional[str] = None, address: Optional[str] = None,
                has_whatsapp: bool = False, source_url: Optional[str] = None) -> int:
    """Insert a lead, ignoring duplicates on (phone_wa, niche).

    Returns the lead id (existing row's id on conflict).
    """
    cur = conn.execute(
        """
        INSERT INTO leads (name, phone, phone_wa, city, niche, rubric, address,
                           has_whatsapp, source_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(phone_wa, niche) DO NOTHING
        """,
        (name, phone, phone_wa, city, niche, rubric, address,
         int(has_whatsapp), source_url, _now()),
    )
    if cur.lastrowid and cur.rowcount:
        return cur.lastrowid
    row = conn.execute(
        'SELECT id FROM leads WHERE phone_wa IS ? AND niche IS ?',
        (phone_wa, niche),
    ).fetchone()
    return int(row['id']) if row else 0


def leads_for_niche(conn: sqlite3.Connection, niche: str,
                    city: Optional[str] = None) -> list[dict[str, Any]]:
    """All leads captured under a niche (optionally filtered by city)."""
    if city:
        rows = conn.execute(
            'SELECT * FROM leads WHERE niche = ? AND city = ? ORDER BY id',
            (niche, city),
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM leads WHERE niche = ? ORDER BY id', (niche,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- sites ----------------------------------------------------------------

def create_site(conn: sqlite3.Connection, *, niche: str, city: Optional[str],
                slug: str, url: Optional[str] = None,
                status: str = 'built') -> int:
    """Register a generated site; returns its id."""
    cur = conn.execute(
        """
        INSERT INTO sites (niche, city, slug, url, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            niche=excluded.niche, city=excluded.city,
            url=excluded.url, status=excluded.status
        """,
        (niche, city, slug, url, status, _now()),
    )
    if cur.lastrowid and cur.rowcount:
        return cur.lastrowid
    row = conn.execute('SELECT id FROM sites WHERE slug = ?', (slug,)).fetchone()
    return int(row['id']) if row else 0


def set_site_status(conn: sqlite3.Connection, site_id: int, status: str,
                    url: Optional[str] = None) -> None:
    """Update deployment status (and url) of a site."""
    if url is not None:
        conn.execute('UPDATE sites SET status = ?, url = ? WHERE id = ?',
                     (status, url, site_id))
    else:
        conn.execute('UPDATE sites SET status = ? WHERE id = ?',
                     (status, site_id))


def mark_site_deployed(conn: sqlite3.Connection, slug: str,
                       url: Optional[str]) -> None:
    """Flag a site (by slug) as deployed and store its public URL."""
    conn.execute("UPDATE sites SET status = 'deployed', url = ? WHERE slug = ?",
                 (url, slug))


# --- campaigns & messages -------------------------------------------------

def create_campaign(conn: sqlite3.Connection, *, niche: str, city: Optional[str],
                    message_template: str, site_id: Optional[int] = None,
                    status: str = 'draft') -> int:
    """Create a campaign; returns its id."""
    cur = conn.execute(
        """
        INSERT INTO campaigns (site_id, niche, city, message_template, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (site_id, niche, city, message_template, status, _now()),
    )
    return int(cur.lastrowid)


def set_campaign_status(conn: sqlite3.Connection, campaign_id: int, status: str) -> None:
    conn.execute('UPDATE campaigns SET status = ? WHERE id = ?', (status, campaign_id))


def queue_messages(conn: sqlite3.Connection, campaign_id: int,
                   lead_ids: list[int]) -> int:
    """Queue one message per lead (skips leads already queued). Returns queued count."""
    n = 0
    for lead_id in lead_ids:
        cur = conn.execute(
            """
            INSERT INTO messages (campaign_id, lead_id, status)
            VALUES (?, ?, 'queued')
            ON CONFLICT(campaign_id, lead_id) DO NOTHING
            """,
            (campaign_id, lead_id),
        )
        if cur.rowcount:
            n += 1
    return n


def next_queued(conn: sqlite3.Connection, campaign_id: int) -> Optional[dict[str, Any]]:
    """The next queued message joined with its lead (phone/name), or None."""
    row = conn.execute(
        """
        SELECT m.id AS msg_id, m.lead_id, l.name, l.phone_wa, l.phone, l.city
        FROM messages m JOIN leads l ON l.id = m.lead_id
        WHERE m.campaign_id = ? AND m.status = 'queued'
        ORDER BY m.id LIMIT 1
        """,
        (campaign_id,),
    ).fetchone()
    return dict(row) if row else None


def set_message_status(conn: sqlite3.Connection, msg_id: int, status: str,
                       error: Optional[str] = None) -> None:
    """Update a message's status; stamps sent_at for terminal send states."""
    sent_at = _now() if status in ('sent', 'delivered') else None
    conn.execute('UPDATE messages SET status = ?, error = ?, sent_at = ? WHERE id = ?',
                 (status, error, sent_at, msg_id))


def campaign_stats(conn: sqlite3.Connection, campaign_id: int) -> dict[str, int]:
    """Counts of messages per status for a campaign."""
    rows = conn.execute(
        'SELECT status, COUNT(*) AS n FROM messages WHERE campaign_id = ? GROUP BY status',
        (campaign_id,),
    ).fetchall()
    stats = {'total': 0, 'queued': 0, 'sent': 0, 'delivered': 0, 'failed': 0}
    for r in rows:
        stats[r['status']] = int(r['n'])
        stats['total'] += int(r['n'])
    return stats


def count_sent_today(conn: sqlite3.Connection) -> int:
    """Messages actually sent today (UTC), across all campaigns (daily-limit guard)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM messages
        WHERE status IN ('sent', 'delivered') AND substr(sent_at, 1, 10) = date('now')
        """
    ).fetchone()
    return int(row['n'])
