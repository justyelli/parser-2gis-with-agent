from __future__ import annotations

import random
import threading
import time
from datetime import datetime
from typing import Any, Optional

import requests

from ..logger import logger
from . import db
from .options import OutreachOptions
from .sitegen import client_slugs, slugify


class CampaignRunner:
    """Background WhatsApp broadcast for one niche at a time.

    Loads the niche's leads, queues a message per lead, and sends them through
    the Node gateway with anti-ban pacing: a random delay between messages, a
    daily cap, and a working-hours window. Status is tracked per message in the
    DB so a run can be inspected or resumed.

    A single campaign runs at a time (mirrors the parser's ParseJob).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancelled = False
        self.status = 'idle'   # idle | running | paused | done | stopped | error
        self.campaign_id: Optional[int] = None
        self.current: Optional[str] = None   # name of the lead being messaged
        self.error: Optional[str] = None
        self.dry_run = False

    @property
    def running(self) -> bool:
        return self.status == 'running'

    def start(self, opts: OutreachOptions, gateway_url: str, *, niche: str,
              city: Optional[str], message_template: str,
              link: Optional[str] = None, dry_run: bool = False,
              ai_personalize: bool = False) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError('Рассылка уже запущена')
            self.status = 'running'
            self.error = None
            self.current = None
            self.campaign_id = None
            self._cancelled = False
            self.dry_run = dry_run

        self._thread = threading.Thread(
            target=self._run,
            args=(opts, gateway_url, niche, city, message_template, link,
                  dry_run, ai_personalize),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._cancelled = True

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _within_hours(opts: OutreachOptions) -> bool:
        """Whether the current local hour is inside the allowed send window."""
        h = datetime.now().hour
        start, end = opts.send_hours_start, opts.send_hours_end
        if start == end:
            return True  # window disabled
        if start < end:
            return start <= h < end
        return h >= start or h < end  # window wraps past midnight

    def _send_one(self, gateway_url: str, phone_wa: str, text: str,
                  dry_run: bool) -> tuple[bool, Optional[str]]:
        """Send a single message via the gateway. Returns (ok, error)."""
        if dry_run:
            return True, None
        try:
            r = requests.post(gateway_url.rstrip('/') + '/send',
                              json={'phone': phone_wa, 'message': text}, timeout=30)
            body = r.json() if r.content else {}
            if r.status_code == 200 and body.get('ok'):
                return True, None
            return False, str(body.get('error') or ('HTTP ' + str(r.status_code)))
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _render(template: str, *, name: Optional[str], link: Optional[str]) -> str:
        """Fill {name} / {link} placeholders in the message template."""
        return (template
                .replace('{name}', name or '')
                .replace('{link}', link or ''))

    def _compose(self, msg: dict, message_template: str, link: Optional[str],
                 ai_personalize: bool, writer_client: Any, model: str) -> str:
        """Build the message body for one lead.

        In AI mode, ``message_template`` is treated as the sender's brief/offer
        and GLM writes a unique message per business. Any failure falls back to
        the plain {name}/{link} template so the campaign never stalls.
        """
        if ai_personalize and writer_client is not None:
            try:
                from . import messagegen
                return messagegen.generate_message(
                    msg, link=link, brief=message_template,
                    model=model, client=writer_client)
            except Exception as e:
                logger.warning('  ИИ не смог написать для «%s» (%s) — шаблон.',
                               msg.get('name'), e)
        return self._render(message_template, name=msg.get('name'), link=link)

    # --- worker -----------------------------------------------------------

    def _run(self, opts: OutreachOptions, gateway_url: str, niche: str,
             city: Optional[str], message_template: str,
             link: Optional[str], dry_run: bool,
             ai_personalize: bool = False) -> None:
        try:
            with db.session() as conn:
                leads = db.leads_for_niche(conn, niche, city)
                if not leads:
                    self.status = 'done'
                    logger.info('Рассылка: лидов по нише «%s» нет.', niche)
                    return
                campaign_id = db.create_campaign(
                    conn, niche=niche, city=city,
                    message_template=message_template, status='running')
                db.queue_messages(conn, campaign_id, [int(l['id']) for l in leads])
            self.campaign_id = campaign_id

            # Build the GLM client once (reused for every lead). If it can't be
            # set up (no key/package), fall back to the plain template so the
            # campaign still runs.
            writer_client = None
            if ai_personalize:
                try:
                    from . import llm
                    writer_client = llm.make_client()
                except Exception as e:
                    logger.warning('ИИ-персонализация выключена (%s) — обычный шаблон.', e)
                    ai_personalize = False

            logger.info('Рассылка запущена: ниша «%s», лидов %d%s%s',
                        niche, len(leads), ' (dry-run)' if dry_run else '',
                        ' · ИИ-персонализация' if ai_personalize else '')

            # Per-client personal page URL: <niche>.<domain>/<client-slug>.
            scheme = 'https' if opts.use_https else 'http'
            niche_slug = slugify(niche + ('-' + city if city else ''))
            slug_by_lead_id = {
                int(lead['id']): client_slug
                for lead, client_slug in client_slugs(leads)
                if lead.get('id') is not None
            }

            while not self._cancelled:
                if not dry_run and not self._within_hours(opts):
                    self.status = 'paused'
                    logger.info('Рассылка на паузе: вне рабочих часов (%d–%d).',
                                opts.send_hours_start, opts.send_hours_end)
                    break

                with db.session() as conn:
                    if not dry_run and db.count_sent_today(conn) >= opts.send_daily_limit:
                        self.status = 'paused'
                        logger.info('Рассылка на паузе: достигнут дневной лимит (%d).',
                                    opts.send_daily_limit)
                        break
                    msg = db.next_queued(conn, campaign_id)

                if not msg:
                    self.status = 'done'
                    break

                self.current = msg['name']
                # Personal link per client: public domain when configured,
                # otherwise append the client path to the form's base link.
                client_slug = slug_by_lead_id.get(int(msg['lead_id']),
                                                  slugify(msg['name']))
                if opts.base_domain:
                    per_link = f'{scheme}://{niche_slug}.{opts.base_domain}/{client_slug}'
                elif link:
                    per_link = link.rstrip('/') + '/' + client_slug
                else:
                    per_link = None
                text = self._compose(msg, message_template, per_link,
                                     ai_personalize, writer_client, opts.llm_model)
                ok, err = self._send_one(gateway_url, msg['phone_wa'], text, dry_run)

                with db.session() as conn:
                    db.set_message_status(conn, int(msg['msg_id']),
                                          'sent' if ok else 'failed', err, text=text)
                logger.info('  %s %s%s', '✓' if ok else '✗', msg['name'],
                            '' if ok else (' — ' + str(err)))

                # Anti-ban pacing: random gap between messages.
                if not self._cancelled:
                    delay = 0.05 if dry_run else random.uniform(
                        opts.send_delay_min, opts.send_delay_max)
                    self._sleep(delay)

            if self._cancelled and self.status == 'running':
                self.status = 'stopped'
            with db.session() as conn:
                db.set_campaign_status(conn, campaign_id,
                                       'done' if self.status == 'done' else self.status)
            logger.info('Рассылка %s.', {'done': 'завершена', 'stopped': 'остановлена',
                                          'paused': 'на паузе'}.get(self.status, self.status))
        except Exception as e:
            self.error = str(e)
            self.status = 'error'
            logger.error('Ошибка рассылки: %s', e, exc_info=True)
        finally:
            self.current = None

    def _sleep(self, seconds: float) -> None:
        """Sleep in small slices so stop() takes effect promptly."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self._cancelled:
            time.sleep(min(0.2, end - time.monotonic()))

    # --- status -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Progress info for the dashboard."""
        stats = {'total': 0, 'queued': 0, 'sent': 0, 'delivered': 0, 'failed': 0}
        if self.campaign_id is not None:
            try:
                with db.session() as conn:
                    stats = db.campaign_stats(conn, self.campaign_id)
            except Exception:
                pass
        return {
            'status': self.status,
            'running': self.running,
            'campaign_id': self.campaign_id,
            'current': self.current,
            'error': self.error,
            'dry_run': self.dry_run,
            'stats': stats,
        }

    def leads_count(self, niche: str, city: Optional[str] = None) -> int:
        with db.session() as conn:
            return len(db.leads_for_niche(conn, niche, city))
