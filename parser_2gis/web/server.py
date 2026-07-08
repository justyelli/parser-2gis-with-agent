from __future__ import annotations

import hmac
import json
import os
import tempfile
import webbrowser
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests

from ..config import Configuration
from ..logger import logger
from ..outreach import CampaignRunner
from ..paths import data_path
from ..writer import WriterOptions, get_writer
from .history import History
from .job import ParseJob

# Download file names per format.
_DOWNLOAD_NAMES = {'csv': '2gis.csv', 'xlsx': '2gis.xlsx',
                   'json': '2gis.json', 'html': '2gis.html'}

# Country code -> human name (for the link generator).
COUNTRIES = {
    'ru': 'Россия', 'kz': 'Казахстан', 'by': 'Беларусь', 'az': 'Азербайджан',
    'kg': 'Киргизия', 'uz': 'Узбекистан', 'cz': 'Чехия', 'eg': 'Египет',
    'it': 'Италия', 'sa': 'Саудовская Аравия', 'cy': 'Кипр', 'ae': 'ОАЭ',
    'cl': 'Чили', 'qa': 'Катар', 'om': 'Оман', 'bh': 'Бахрейн',
    'kw': 'Кувейт', 'iq': 'Ирак',
}


def _load_dotenv() -> None:
    """Fill missing env vars from a project-root .env file.

    Real environment variables always win (shell exports, systemd
    EnvironmentFile), so this only backfills what's absent. Uses python-dotenv
    when installed, else a tiny built-in parser — no extra dependency required.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
        return
    except ImportError:
        pass
    candidates = (Path.cwd() / '.env', Path(__file__).resolve().parents[2] / '.env')
    for env_file in candidates:
        if not env_file.is_file():
            continue
        for raw in env_file.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break


@lru_cache(maxsize=1)
def _load_cities() -> list[dict[str, Any]]:
    with open(data_path() / 'cities.json', 'r', encoding='utf-8') as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_rubrics() -> list[dict[str, Any]]:
    """Flat list of rubrics for the web generator picker."""
    with open(data_path() / 'rubrics.json', 'r', encoding='utf-8') as f:
        rubrics = json.load(f)
    out = []
    for node in rubrics.values():
        # Skip the synthetic root and group headers without a usable label.
        if node.get('code') in (None, '0') or not node.get('label'):
            continue
        out.append({
            'code': node['code'],
            'label': node['label'],
            'is_russian': bool(node.get('isRussian', True)),
            'is_non_russian': bool(node.get('isNonRussian', True)),
        })
    out.sort(key=lambda r: r['label'].lower())
    return out


def _build_config(data: dict[str, Any]) -> Configuration:
    """Build a Configuration from the web request payload."""
    config = Configuration()
    config.chrome.headless = bool(data.get('headless', True))
    config.parser.max_records = max(1, int(data.get('max_records', 100)))
    config.writer.csv.clean = bool(data.get('clean', True))

    adv = data.get('advanced', {}) or {}
    if adv:
        config.chrome.disable_images = bool(adv.get('disable_images', config.chrome.disable_images))
        config.chrome.start_maximized = bool(adv.get('start_maximized', config.chrome.start_maximized))
        if adv.get('memory_limit'):
            config.chrome.memory_limit = max(1, int(adv['memory_limit']))
        config.parser.skip_404_response = bool(adv.get('skip_404_response', config.parser.skip_404_response))
        config.parser.delay_between_clicks = max(0, int(adv.get('delay_between_clicks', 0) or 0))
        config.writer.csv.add_rubrics = bool(adv.get('add_rubrics', config.writer.csv.add_rubrics))
        config.writer.csv.add_comments = bool(adv.get('add_comments', config.writer.csv.add_comments))
        config.writer.csv.remove_empty_columns = bool(adv.get('remove_empty_columns', config.writer.csv.remove_empty_columns))
        config.writer.csv.remove_duplicates = bool(adv.get('remove_duplicates', config.writer.csv.remove_duplicates))
        if adv.get('columns_per_entity'):
            config.writer.csv.columns_per_entity = min(5, max(1, int(adv['columns_per_entity'])))
        if adv.get('encoding'):
            config.writer.encoding = str(adv['encoding'])

    f = data.get('filters', {}) or {}
    config.filters.dedup_franchises = bool(f.get('dedup_franchises'))
    config.filters.dedup_across_niches = bool(f.get('dedup_across_niches', True))
    config.filters.require_phone = bool(f.get('require_phone'))
    config.filters.require_whatsapp = bool(f.get('require_whatsapp'))
    config.filters.require_social = bool(f.get('require_social'))
    config.filters.require_email = bool(f.get('require_email'))
    config.filters.require_website = bool(f.get('require_website'))
    config.filters.require_no_website = bool(f.get('require_no_website'))
    config.filters.min_rating = float(f.get('min_rating', 0) or 0)
    config.filters.min_reviews = int(f.get('min_reviews', 0) or 0)

    # Outreach: capture leads for a niche when the platform is enabled.
    o = data.get('outreach', {}) or {}
    config.outreach.enabled = bool(o.get('enabled'))
    return config


def _login_page(error: str = '') -> str:
    """Minimal self-contained login page for the dashboard."""
    err_html = f'<div class="err">{error}</div>' if error else ''
    return f'''<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход — Parser 2GIS</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;min-height:100vh;
       display:flex;align-items:center;justify-content:center;
       background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:20px}}
  .card{{background:#fff;border-radius:16px;padding:34px 30px;width:100%;max-width:360px;
        box-shadow:0 20px 50px rgba(0,0,0,.25)}}
  h1{{font-size:22px;color:#1a2233;margin-bottom:4px}}
  p.sub{{color:#5b6577;font-size:14px;margin-bottom:22px}}
  label{{display:block;font-size:13px;color:#5b6577;margin:12px 0 6px}}
  input{{width:100%;padding:12px 14px;border:1px solid #e6e8ef;border-radius:10px;font-size:15px}}
  input:focus{{outline:none;border-color:#4f46e5;box-shadow:0 0 0 3px rgba(79,70,229,.15)}}
  button{{width:100%;margin-top:22px;background:#4f46e5;color:#fff;border:none;border-radius:10px;
         padding:13px;font-size:15px;font-weight:700;cursor:pointer}}
  button:hover{{filter:brightness(.95)}}
  .err{{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;border-radius:10px;
       padding:10px 12px;font-size:14px;margin-bottom:16px}}
</style></head><body>
<form class="card" method="post" action="/login">
  <h1>🚀 Панель рассылки</h1>
  <p class="sub">Введите логин и пароль для входа</p>
  {err_html}
  <label>Логин</label>
  <input name="user" autocomplete="username" autofocus>
  <label>Пароль</label>
  <input name="password" type="password" autocomplete="current-password">
  <button type="submit">Войти</button>
</form>
</body></html>'''


def create_app():
    """Create the Flask app for the dashboard. Requires the `web` extra."""
    _load_dotenv()
    try:
        from flask import (Flask, Response, jsonify, redirect, request,
                           send_file, send_from_directory, session)
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            'Для веб-интерфейса нужен Flask. Установите: pip install "parser-2gis[web]"'
        ) from e

    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    app = Flask(__name__, static_folder=static_dir, static_url_path='/static')
    job = ParseJob()
    history = History()
    campaign = CampaignRunner()

    # ---- Access control (optional login) --------------------------------
    # Enabled only when PANEL_PASSWORD is set, so local/dev use isn't broken.
    # Protects the whole dashboard incl. all /api/* (parser, WhatsApp send…);
    # generated client sites live on their own subdomains and stay public.
    panel_user = os.getenv('PANEL_USER', 'admin')
    panel_password = os.getenv('PANEL_PASSWORD', '')
    auth_on = bool(panel_password)
    app.secret_key = os.getenv('PANEL_SECRET_KEY') or os.urandom(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    if not auth_on:
        logger.warning('PANEL_PASSWORD не задан — панель открыта без пароля.')

    @app.before_request
    def _require_login():
        if not auth_on or request.path == '/login' or session.get('auth'):
            return None
        if request.path.startswith('/api/'):
            return jsonify({'ok': False, 'error': 'Требуется вход'}), 401
        return redirect('/login')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if not auth_on:
            return redirect('/')
        error = ''
        if request.method == 'POST':
            ok = (hmac.compare_digest(request.form.get('user', ''), panel_user)
                  and hmac.compare_digest(request.form.get('password', ''), panel_password))
            if ok:
                session['auth'] = True
                return redirect('/')
            error = 'Неверный логин или пароль'
        return Response(_login_page(error), mimetype='text/html')

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect('/login')

    def _export_send(docs, writer_opts: WriterOptions, fmt: str):
        """Write `docs` to a temp file in `fmt` and send it as a download."""
        tmp_dir = tempfile.mkdtemp(prefix='p2gis_web_')
        out_path = os.path.join(tmp_dir, _DOWNLOAD_NAMES[fmt])
        with get_writer(out_path, fmt, writer_opts) as writer:
            for doc in docs:
                writer.write(doc)
        return send_file(out_path, as_attachment=True, download_name=_DOWNLOAD_NAMES[fmt])

    @app.route('/')
    def index():
        return send_from_directory(static_dir, 'index.html')

    @app.route('/api/start', methods=['POST'])
    def api_start():
        data = request.get_json(force=True, silent=True) or {}
        urls = [u.strip() for u in (data.get('urls') or []) if u and u.strip()]
        if not urls:
            return jsonify({'ok': False, 'error': 'Не указаны ссылки'}), 400
        try:
            config = _build_config(data)
            o = data.get('outreach', {}) or {}
            niche = (o.get('niche') or '').strip() or None
            city = (o.get('city') or '').strip() or None
            job.start(config, urls, niche=niche, city=city)
        except RuntimeError as e:
            return jsonify({'ok': False, 'error': str(e)}), 409
        except Exception as e:
            logger.error('Не удалось запустить парсинг: %s', e)
            return jsonify({'ok': False, 'error': str(e)}), 400
        return jsonify({'ok': True})

    @app.route('/api/stop', methods=['POST'])
    def api_stop():
        job.stop()
        return jsonify({'ok': True})

    @app.route('/api/clear', methods=['POST'])
    def api_clear():
        return jsonify({'ok': job.clear()})

    @app.route('/api/status')
    def api_status():
        cursor = request.args.get('cursor', default=0, type=int)
        logs = job.logs[cursor:]
        return jsonify({
            'status': job.status,
            'running': job.running,
            'count': job.count,
            'error': job.error,
            'logs': logs,
            'cursor': cursor + len(logs),
        })

    @app.route('/api/results')
    def api_results():
        return jsonify({'records': job.results()})

    @app.route('/api/generator')
    def api_generator():
        """Data for the link generator: countries, cities, rubrics."""
        cities = [
            {'name': c['name'], 'code': c['code'], 'domain': c['domain'],
             'country_code': c['country_code']}
            for c in _load_cities()
        ]
        countries = [{'code': k, 'name': v} for k, v in COUNTRIES.items()]
        countries.sort(key=lambda c: c['name'])
        return jsonify({'countries': countries, 'cities': cities, 'rubrics': _load_rubrics()})

    @app.route('/api/download')
    def api_download():
        fmt = request.args.get('format', 'csv')
        if fmt not in _DOWNLOAD_NAMES:
            return jsonify({'ok': False, 'error': 'Неизвестный формат'}), 400
        if not job.count:
            return jsonify({'ok': False, 'error': 'Нет данных'}), 400

        try:
            assert job.collector is not None
            return _export_send(job.collector.docs, job.collector._options, fmt)
        except Exception as e:
            logger.error('Ошибка экспорта: %s', e)
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/history')
    def api_history():
        return jsonify({'items': history.list()})

    @app.route('/api/history/<hid>/results')
    def api_history_results(hid):
        docs = history.docs(hid)
        if docs is None:
            return jsonify({'ok': False, 'error': 'Запись не найдена'}), 404
        return jsonify({'records': history.records(hid)})

    @app.route('/api/history/<hid>/download')
    def api_history_download(hid):
        fmt = request.args.get('format', 'csv')
        if fmt not in _DOWNLOAD_NAMES:
            return jsonify({'ok': False, 'error': 'Неизвестный формат'}), 400
        docs = history.docs(hid)
        if not docs:
            return jsonify({'ok': False, 'error': 'Запись не найдена'}), 404
        try:
            opts = WriterOptions(**history.writer_options(hid))
        except Exception:
            opts = WriterOptions()
        try:
            return _export_send(docs, opts, fmt)
        except Exception as e:
            logger.error('Ошибка экспорта истории: %s', e)
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/history/merge', methods=['POST'])
    def api_history_merge():
        data = request.get_json(force=True, silent=True) or {}
        ids = [str(i) for i in (data.get('ids') or [])]
        if not ids:
            return jsonify({'ok': False, 'error': 'Не выбраны записи'}), 400
        result = history.merge_and_save(ids)
        if not result:
            return jsonify({'ok': False, 'error': 'Нет данных для объединения'}), 400
        new_id, count = result
        return jsonify({'ok': True, 'id': new_id, 'count': count})

    @app.route('/api/history/<hid>', methods=['DELETE'])
    def api_history_delete(hid):
        return jsonify({'ok': history.delete(hid)})

    # ---- WhatsApp gateway proxy (Baileys, Node service) ----
    # The dashboard talks to the gateway only through these routes, so the
    # browser never hits the Node service directly (no CORS, one origin).
    def _outreach_opts():
        """Outreach settings with env overrides (set these on the VPS)."""
        o = Configuration().outreach
        o.gateway_url = os.getenv('WA_GATEWAY_URL', o.gateway_url)
        o.base_domain = os.getenv('OUTREACH_BASE_DOMAIN', o.base_domain)
        o.sites_dir = os.getenv('OUTREACH_SITES_DIR', o.sites_dir)
        o.llm_model = os.getenv('OUTREACH_MODEL', o.llm_model)
        # HTTPS only works once a wildcard cert is installed (see enable-ssl.sh);
        # until then keep links on http so they open. Controlled per-VPS by env.
        https_env = os.getenv('OUTREACH_USE_HTTPS')
        if https_env is not None:
            o.use_https = https_env.strip().lower() in ('1', 'true', 'yes', 'on')
        return o

    def _wa_url(path: str) -> str:
        base = _outreach_opts().gateway_url.rstrip('/')
        return base + path

    @app.route('/api/wa/status')
    def api_wa_status():
        try:
            r = requests.get(_wa_url('/status'), timeout=5)
            return jsonify(r.json())
        except Exception:
            return jsonify({'connected': False, 'hasQr': False,
                            'user': None, 'error': 'gateway_offline'})

    @app.route('/api/wa/qr')
    def api_wa_qr():
        try:
            r = requests.get(_wa_url('/qr'), timeout=5)
            return jsonify(r.json())
        except Exception:
            return jsonify({'qr': None, 'error': 'gateway_offline'})

    @app.route('/api/wa/logout', methods=['POST'])
    def api_wa_logout():
        try:
            r = requests.post(_wa_url('/logout'), timeout=10)
            return jsonify(r.json())
        except Exception:
            return jsonify({'ok': False, 'error': 'gateway_offline'}), 502

    @app.route('/api/wa/send', methods=['POST'])
    def api_wa_send():
        data = request.get_json(force=True, silent=True) or {}
        try:
            r = requests.post(_wa_url('/send'), json=data, timeout=30)
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({'ok': False, 'error': 'gateway_offline'}), 502

    # ---- Campaigns (WhatsApp broadcast, step 6) ----
    @app.route('/api/campaign/leads')
    def api_campaign_leads():
        niche = (request.args.get('niche') or '').strip()
        city = (request.args.get('city') or '').strip() or None
        if not niche:
            return jsonify({'count': 0})
        return jsonify({'count': campaign.leads_count(niche, city)})

    @app.route('/api/campaign/start', methods=['POST'])
    def api_campaign_start():
        data = request.get_json(force=True, silent=True) or {}
        niche = (data.get('niche') or '').strip()
        city = (data.get('city') or '').strip() or None
        message = (data.get('message') or '').strip()
        link = (data.get('link') or '').strip() or None
        dry_run = bool(data.get('dry_run'))
        ai_personalize = bool(data.get('ai_personalize'))
        if not niche or not message:
            return jsonify({'ok': False, 'error': 'Нужны ниша и текст сообщения'}), 400
        opts = _outreach_opts()
        gw = opts.gateway_url
        try:
            campaign.start(opts, gw, niche=niche, city=city,
                           message_template=message, link=link, dry_run=dry_run,
                           ai_personalize=ai_personalize)
        except RuntimeError as e:
            return jsonify({'ok': False, 'error': str(e)}), 409
        except Exception as e:
            logger.error('Не удалось запустить рассылку: %s', e)
            return jsonify({'ok': False, 'error': str(e)}), 400
        return jsonify({'ok': True})

    @app.route('/api/campaign/status')
    def api_campaign_status():
        return jsonify(campaign.snapshot())

    @app.route('/api/campaign/stop', methods=['POST'])
    def api_campaign_stop():
        campaign.stop()
        return jsonify({'ok': True})

    # ---- AI site generation (step 3) ----
    @app.route('/api/site/generate', methods=['POST'])
    def api_site_generate():
        from ..outreach import db as odb
        from ..outreach import sitegen
        data = request.get_json(force=True, silent=True) or {}
        niche = (data.get('niche') or '').strip()
        city = (data.get('city') or '').strip() or None
        phone = (data.get('phone') or '').strip() or None
        if not niche:
            return jsonify({'ok': False, 'error': 'Не указана ниша'}), 400
        opts = _outreach_opts()
        try:
            info = sitegen.build_site(niche, city, model=opts.llm_model, phone=phone)
        except RuntimeError as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
        except Exception as e:
            logger.error('Ошибка генерации сайта: %s', e)
            return jsonify({'ok': False, 'error': str(e)}), 500

        url = None
        if opts.base_domain:
            scheme = 'https' if opts.use_https else 'http'
            url = f"{scheme}://{info['slug']}.{opts.base_domain}"
        try:
            with odb.session() as conn:
                odb.create_site(conn, niche=niche, city=city, slug=info['slug'],
                                url=url, status='built')
        except Exception as e:
            logger.error('Не удалось сохранить сайт в БД: %s', e)
        return jsonify({'ok': True, 'slug': info['slug'], 'url': url,
                        'preview_url': f"/api/site/preview/{info['slug']}/"})

    @app.route('/api/site/preview/<slug>/')
    @app.route('/api/site/preview/<slug>')
    def api_site_preview(slug):
        from ..outreach import sitegen
        site_dir = sitegen.local_sites_dir() / slug
        if not (site_dir / 'index.html').exists():
            return jsonify({'ok': False, 'error': 'Сайт не найден'}), 404
        return send_from_directory(str(site_dir), 'index.html')

    # ---- Deploy site to a subdomain (step 4) ----
    @app.route('/api/site/deploy', methods=['POST'])
    def api_site_deploy():
        from ..outreach import db as odb
        from ..outreach import deploy as odeploy
        data = request.get_json(force=True, silent=True) or {}
        slug = (data.get('slug') or '').strip()
        if not slug:
            return jsonify({'ok': False, 'error': 'Не указан сайт'}), 400
        opts = _outreach_opts()
        try:
            info = odeploy.deploy_site(slug, opts)
        except RuntimeError as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
        except Exception as e:
            logger.error('Ошибка публикации сайта: %s', e)
            return jsonify({'ok': False, 'error': str(e)}), 500
        try:
            with odb.session() as conn:
                odb.mark_site_deployed(conn, slug, info.get('url'))
        except Exception as e:
            logger.error('Не удалось обновить статус сайта: %s', e)
        return jsonify({'ok': True, 'url': info.get('url'), 'path': info.get('path')})

    return app


def run_server(host: str = '127.0.0.1', port: int = 8666, open_browser: bool = True) -> None:
    """Run the dashboard server (blocking)."""
    app = create_app()
    url = f'http://{host}:{port}/'
    logger.info('Веб-интерфейс запущен: %s', url)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
