from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..logger import logger
from ..writer.record import extract_record
from . import db
from .phone import to_wa_number


def capture_leads(docs: list[Any], *, niche: str, city_hint: Optional[str] = None,
                  db_path: Optional[Path] = None) -> int:
    """Store parsed businesses as outreach leads under `niche`.

    Intended to run on the documents that already passed the parse filters
    (e.g. the "no website" filter), so every stored lead is a target for the
    campaign. Leads without any usable phone are skipped. Deduplication on
    (phone_wa, niche) happens at the DB layer.

    Args:
        docs: Raw catalog documents collected during a parse run.
        niche: Rubric label the run was performed under (campaign key).
        city_hint: City to fall back on when a record has none.
        db_path: Override the outreach DB path (mainly for tests).

    Returns:
        Number of new leads inserted (duplicates within the niche don't count).
    """
    processed = 0
    with db.session(db_path) as conn:
        before = _count(conn, niche)
        for doc in docs:
            record = extract_record(doc)
            if not record:
                continue

            contacts = record.get('contacts', {})
            # Prefer the explicit WhatsApp contact; fall back to the phone.
            raw = contacts.get('whatsapp') or contacts.get('phone')
            phone_wa = to_wa_number(raw)
            if not phone_wa:
                continue

            db.upsert_lead(
                conn,
                name=record['name'],
                phone=contacts.get('phone'),
                phone_wa=phone_wa,
                city=record.get('city') or city_hint,
                niche=niche,
                address=record.get('address'),
                has_whatsapp='whatsapp' in contacts,
                source_url=record.get('url'),
                logo_url=record.get('logo_url'),
            )
            processed += 1
        inserted = _count(conn, niche) - before

    logger.info('Захвачено лидов: %d новых (обработано записей с телефоном: %d)',
                inserted, processed)
    return inserted


def _count(conn: Any, niche: str) -> int:
    row = conn.execute('SELECT COUNT(*) AS n FROM leads WHERE niche = ?',
                       (niche,)).fetchone()
    return int(row['n'])
