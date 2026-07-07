from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from ..logger import logger
from ..paths import user_path

# Cyrillic -> Latin transliteration for building subdomain slugs.
_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'қ': 'q', 'ғ': 'g', 'ұ': 'u', 'ү': 'u', 'ө': 'o', 'һ': 'h',
    'і': 'i', 'ә': 'a', 'ң': 'ng',
}

# JSON schema the model must fill (structured output).
_SITE_SCHEMA = {
    'type': 'object',
    'properties': {
        'business_type': {'type': 'string', 'description': 'Красивое название типа бизнеса, напр. «Стоматология»'},
        'hero_title': {'type': 'string'},
        'hero_subtitle': {'type': 'string'},
        'about': {'type': 'string', 'description': '2-3 предложения о бизнесе'},
        'services': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'description': {'type': 'string'},
                },
                'required': ['title', 'description'],
                'additionalProperties': False,
            },
        },
        'advantages': {'type': 'array', 'items': {'type': 'string'}},
        'cta_text': {'type': 'string'},
    },
    'required': ['business_type', 'hero_title', 'hero_subtitle', 'about',
                'services', 'advantages', 'cta_text'],
    'additionalProperties': False,
}

_SYSTEM = (
    'Ты — копирайтер и маркетолог, пишешь тексты для сайта-визитки малого бизнеса '
    'на русском языке. Пиши живо, конкретно и по делу, без канцелярита и «воды». '
    'Верни ТОЛЬКО данные по заданной схеме.'
)


def slugify(text: str) -> str:
    """Build a URL-safe subdomain slug from Russian/Kazakh text."""
    text = (text or '').lower().strip()
    out = []
    for ch in text:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        elif ch in (' ', '-', '_'):
            out.append('-')
        # drop everything else
    slug = re.sub(r'-+', '-', ''.join(out)).strip('-')
    return slug or 'site'


def local_sites_dir() -> Path:
    """Where generated sites are written locally (for preview before deploy)."""
    return user_path(is_config=False) / 'sites'


def generate_site_content(niche: str, city: Optional[str], model: str) -> dict[str, Any]:
    """Ask Claude for the site copy (structured JSON) for a niche.

    Requires the `anthropic` package and the ANTHROPIC_API_KEY env var.
    Raises RuntimeError with a friendly message if either is missing.
    """
    if not os.getenv('ANTHROPIC_API_KEY'):
        raise RuntimeError('Не задан ANTHROPIC_API_KEY (ключ Claude API).')
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError('Не установлен пакет anthropic (pip install anthropic).') from e

    client = anthropic.Anthropic()
    where = f' в городе {city}' if city else ''
    prompt = (
        f'Составь тексты для сайта-визитки бизнеса ниши «{niche}»{where}. '
        f'Дай 4-6 услуг и 3-5 преимуществ. Тексты — общие для ниши, без конкретного '
        f'названия компании.'
    )
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=_SYSTEM,
        messages=[{'role': 'user', 'content': prompt}],
        output_config={'format': {'type': 'json_schema', 'schema': _SITE_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == 'text'), None)
    if not text:
        raise RuntimeError('Пустой ответ от Claude API.')
    return json.loads(text)


def render_html(content: dict[str, Any], *, niche: str, city: Optional[str],
                phone: Optional[str] = None) -> str:
    """Render a self-contained, responsive site from the generated content."""
    def esc(s: Any) -> str:
        return (str(s) if s is not None else '').replace('&', '&amp;').replace(
            '<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    title = esc(content.get('business_type') or niche)
    where = f' · {esc(city)}' if city else ''
    services = content.get('services') or []
    advantages = content.get('advantages') or []
    wa_digits = re.sub(r'\D', '', phone or '')
    cta = esc(content.get('cta_text') or 'Связаться')
    cta_href = f'https://wa.me/{wa_digits}' if wa_digits else '#contact'

    services_html = ''.join(
        f'<div class="card"><h3>{esc(s.get("title"))}</h3>'
        f'<p>{esc(s.get("description"))}</p></div>'
        for s in services
    )
    adv_html = ''.join(f'<li>{esc(a)}</li>' for a in advantages)

    return f'''<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}{where}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1a2233;line-height:1.6;background:#fff}}
  .wrap{{max-width:1040px;margin:0 auto;padding:0 20px}}
  header{{background:linear-gradient(135deg,#4f46e5,#7c3aed);color:#fff;padding:80px 0 90px}}
  header h1{{font-size:44px;font-weight:800;line-height:1.15}}
  header p{{font-size:19px;opacity:.92;margin-top:14px;max-width:640px}}
  .btn{{display:inline-block;margin-top:26px;background:#25D366;color:#fff;font-weight:700;
        text-decoration:none;padding:14px 28px;border-radius:12px;font-size:16px}}
  .btn:hover{{filter:brightness(.95)}}
  section{{padding:56px 0}}
  h2{{font-size:30px;font-weight:800;margin-bottom:8px}}
  .lead{{color:#5b6577;margin-bottom:28px;max-width:680px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px}}
  .card{{border:1px solid #e6e8ef;border-radius:14px;padding:22px;background:#fbfbfe}}
  .card h3{{font-size:18px;margin-bottom:6px;color:#4f46e5}}
  .card p{{color:#5b6577;font-size:15px}}
  ul.adv{{list-style:none;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}}
  ul.adv li{{padding:14px 16px 14px 42px;position:relative;background:#f4f4fb;border-radius:10px}}
  ul.adv li:before{{content:"✓";position:absolute;left:16px;color:#25D366;font-weight:800}}
  .contact{{background:#0f1425;color:#fff;text-align:center}}
  .contact h2{{color:#fff}}
  footer{{padding:26px 0;text-align:center;color:#8b93a7;font-size:13px}}
  @media(max-width:600px){{header h1{{font-size:32px}}header{{padding:56px 0 64px}}}}
</style></head><body>
<header><div class="wrap">
  <h1>{esc(content.get('hero_title'))}</h1>
  <p>{esc(content.get('hero_subtitle'))}</p>
  <a class="btn" href="{cta_href}">{cta}</a>
</div></header>

<section><div class="wrap">
  <h2>О нас</h2>
  <p class="lead">{esc(content.get('about'))}</p>
</div></section>

<section style="background:#fafafe"><div class="wrap">
  <h2>Услуги</h2>
  <p class="lead">Что мы предлагаем</p>
  <div class="grid">{services_html}</div>
</div></section>

<section><div class="wrap">
  <h2>Почему мы</h2>
  <ul class="adv">{adv_html}</ul>
</div></section>

<section class="contact" id="contact"><div class="wrap">
  <h2>{cta}</h2>
  <p class="lead" style="color:#aab2c8;margin:10px auto 0">Напишите нам — ответим быстро.</p>
  <a class="btn" href="{cta_href}">Написать в WhatsApp</a>
</div></section>

<footer>{title}{where}</footer>
</body></html>'''


def build_site(niche: str, city: Optional[str], *, model: str,
               phone: Optional[str] = None, out_root: Optional[Path] = None) -> dict[str, Any]:
    """Generate copy, render the page, and write it to <out_root>/<slug>/index.html.

    Returns {'slug', 'dir', 'index_path'}. Does not deploy (that's the deploy step).
    """
    content = generate_site_content(niche, city, model)
    html = render_html(content, niche=niche, city=city, phone=phone)

    base = niche + ('-' + city if city else '')
    slug = slugify(base)
    root = out_root or local_sites_dir()
    site_dir = root / slug
    site_dir.mkdir(parents=True, exist_ok=True)
    index_path = site_dir / 'index.html'
    index_path.write_text(html, encoding='utf-8')

    logger.info('Сайт сгенерирован: %s (%s)', slug, index_path)
    return {'slug': slug, 'dir': str(site_dir), 'index_path': str(index_path)}
