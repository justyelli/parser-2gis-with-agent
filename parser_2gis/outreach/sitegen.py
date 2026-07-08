from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from ..logger import logger
from ..paths import user_path
from . import llm

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
    'Ты — сильный копирайтер и маркетолог, пишешь продающие тексты для сайта-'
    'визитки малого бизнеса на русском языке. Правила:\n'
    '— Пиши живо, конкретно, на языке выгод для клиента; никакого канцелярита, '
    'штампов («широкий спектр услуг», «индивидуальный подход», «команда '
    'профессионалов») и «воды».\n'
    '— Заголовок (hero_title) — короткий и цепляющий (до 60 символов), про '
    'результат для клиента, а не про компанию.\n'
    '— Услуги: конкретные, с понятной пользой в описании (1 предложение).\n'
    '— Преимущества: осязаемые и правдоподобные, без выдуманных цифр, лицензий '
    'и гарантий, которых не может быть у типового бизнеса.\n'
    '— Если указан город — сделай пару формулировок локально-релевантными.\n'
    '— Тексты общие для ниши (без названия конкретной компании).\n'
    'Верни ТОЛЬКО данные по заданной JSON-схеме.'
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
    """Ask GLM for the site copy (structured JSON) for a niche.

    Uses the GLM (Z.ai / Zhipu) OpenAI-compatible API via :mod:`llm`. Requires
    the `openai` package and the GLM_API_KEY env var; GLM_BASE_URL overrides the
    endpoint. Raises RuntimeError with a friendly message on a missing prereq.
    """
    client = llm.make_client()
    where = f' в городе {city}' if city else ''
    schema_json = json.dumps(_SITE_SCHEMA, ensure_ascii=False, indent=2)
    prompt = (
        f'Составь тексты для сайта-визитки бизнеса ниши «{niche}»{where}. '
        f'Дай 4-6 услуг и 3-5 преимуществ. Тексты — общие для ниши, без конкретного '
        f'названия компании.\n\n'
        f'Верни ТОЛЬКО валидный JSON строго по этой JSON-схеме '
        f'(без markdown и пояснений):\n{schema_json}'
    )
    messages = [
        {'role': 'system', 'content': _SYSTEM},
        {'role': 'user', 'content': prompt},
    ]
    text = llm.complete(client, llm.models_for(model), messages, max_tokens=4000)
    return llm.parse_json(text)


# Accent palettes (c1 -> c2 gradient); picked deterministically per niche so
# different niches get a different, but always tasteful, colour scheme.
_PALETTES = [
    ('#4f46e5', '#7c3aed'),  # indigo → violet
    ('#0ea5e9', '#2563eb'),  # sky → blue
    ('#0d9488', '#059669'),  # teal → emerald
    ('#e11d48', '#db2777'),  # rose → pink
    ('#ea580c', '#d97706'),  # orange → amber
]


def render_html(content: dict[str, Any], *, niche: str, city: Optional[str],
                phone: Optional[str] = None) -> str:
    """Render a self-contained, responsive, modern site from the generated copy."""
    def esc(s: Any) -> str:
        return (str(s) if s is not None else '').replace('&', '&amp;').replace(
            '<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    title = esc(content.get('business_type') or niche)
    where = f' · {esc(city)}' if city else ''
    city_e = esc(city) if city else ''
    services = content.get('services') or []
    advantages = content.get('advantages') or []
    wa_digits = re.sub(r'\D', '', phone or '')
    cta = esc(content.get('cta_text') or 'Оставьте заявку — перезвоним')
    wa_href = f'https://wa.me/{wa_digits}' if wa_digits else '#contact'
    tel_href = f'tel:+{wa_digits}' if wa_digits else ''
    phone_e = esc(phone) if phone else ''

    c1, c2 = _PALETTES[sum(ord(ch) for ch in (niche or 'x')) % len(_PALETTES)]

    eyebrow = f'{title}{where}'
    services_html = ''.join(
        f'<article class="card"><h3>{esc(s.get("title"))}</h3>'
        f'<p>{esc(s.get("description"))}</p></article>'
        for s in services
    )
    adv_html = ''.join(
        f'<div class="adv"><span class="adv-ic">✓</span><span>{esc(a)}</span></div>'
        for a in advantages
    )

    footer_meta = ' · '.join(x for x in (city_e, phone_e) if x)
    nav_phone = (f'<a class="nav-phone" href="{tel_href}">{phone_e}</a>'
                 if phone_e else '')
    hero_call = (f'<a class="btn btn-ghost" href="{tel_href}">Позвонить</a>'
                 if tel_href else '')
    contact_call = (f'<a class="btn btn-line" href="{tel_href}">📞 {phone_e}</a>'
                    if phone_e else '')

    return f'''<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}{where}</title>
<meta name="description" content="{esc(content.get('hero_subtitle'))}">
<style>
  :root{{--c1:{c1};--c2:{c2};--ink:#0f172a;--muted:#5b6472;--line:#e8ebf2;--soft:#f7f8fc;--wa:#25D366}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  html{{scroll-behavior:smooth}}
  body{{font-family:'Inter',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);
       line-height:1.65;background:#fff;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1080px;margin:0 auto;padding:0 22px}}
  a{{color:inherit}}
  .btn{{display:inline-flex;align-items:center;gap:8px;font-weight:700;text-decoration:none;
       padding:14px 26px;border-radius:12px;font-size:16px;transition:transform .15s,box-shadow .15s;cursor:pointer;border:0}}
  .btn:hover{{transform:translateY(-2px)}}
  .btn-wa{{background:var(--wa);color:#fff;box-shadow:0 10px 26px rgba(37,211,102,.35)}}
  .btn-ghost{{background:rgba(255,255,255,.14);color:#fff;border:1px solid rgba(255,255,255,.55);backdrop-filter:blur(4px)}}
  .btn-line{{background:#fff;color:var(--ink);border:1px solid var(--line)}}
  .btn-sm{{padding:10px 18px;font-size:14px;border-radius:10px}}

  .nav{{position:sticky;top:0;z-index:50;background:rgba(255,255,255,.82);
       backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}}
  .nav .wrap{{display:flex;align-items:center;justify-content:space-between;height:66px}}
  .brand{{display:flex;align-items:center;gap:9px;font-weight:800;font-size:18px;letter-spacing:-.2px}}
  .brand .dot{{width:12px;height:12px;border-radius:50%;background:linear-gradient(135deg,var(--c1),var(--c2))}}
  .nav-r{{display:flex;align-items:center;gap:16px}}
  .nav-phone{{text-decoration:none;font-weight:700;font-size:15px;white-space:nowrap}}

  .hero{{position:relative;overflow:hidden;color:#fff;
        background:linear-gradient(135deg,var(--c1),var(--c2));padding:104px 0 112px}}
  .hero:before{{content:"";position:absolute;inset:0;
              background:radial-gradient(900px 420px at 82% -8%,rgba(255,255,255,.28),transparent 60%)}}
  .hero:after{{content:"";position:absolute;right:-120px;bottom:-160px;width:420px;height:420px;
             border-radius:50%;background:rgba(255,255,255,.10)}}
  .hero .wrap{{position:relative}}
  .eyebrow{{display:inline-block;background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.35);
          padding:7px 15px;border-radius:100px;font-size:13px;font-weight:600;margin-bottom:20px}}
  .hero h1{{font-size:clamp(32px,5.5vw,56px);font-weight:800;line-height:1.08;letter-spacing:-1px;max-width:820px}}
  .hero .sub{{font-size:clamp(17px,2.2vw,21px);opacity:.94;margin-top:18px;max-width:620px}}
  .cta-row{{display:flex;flex-wrap:wrap;gap:14px;margin-top:34px}}
  @keyframes up{{from{{opacity:0;transform:translateY(16px)}}to{{opacity:1;transform:none}}}}
  .hero .eyebrow,.hero h1,.hero .sub,.hero .cta-row{{animation:up .6s ease both}}
  .hero h1{{animation-delay:.05s}} .hero .sub{{animation-delay:.12s}} .hero .cta-row{{animation-delay:.2s}}

  section{{padding:72px 0}}
  .sec-eyebrow{{color:var(--c1);font-weight:700;font-size:13px;letter-spacing:1.4px;text-transform:uppercase}}
  h2{{font-size:clamp(26px,3.4vw,36px);font-weight:800;letter-spacing:-.6px;margin:8px 0 10px}}
  .lead{{color:var(--muted);font-size:17px;max-width:680px;margin-bottom:34px}}
  .about{{background:var(--soft)}}
  .about p{{font-size:19px;color:#334155;max-width:760px}}

  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px}}
  .card{{position:relative;border:1px solid var(--line);border-radius:18px;padding:28px 24px;background:#fff;
        transition:transform .18s,box-shadow .18s;overflow:hidden}}
  .card:before{{content:"";position:absolute;left:0;top:0;height:3px;width:100%;
              background:linear-gradient(90deg,var(--c1),var(--c2))}}
  .card:hover{{transform:translateY(-4px);box-shadow:0 18px 40px rgba(15,23,42,.10)}}
  .card h3{{font-size:19px;font-weight:700;margin-bottom:8px}}
  .card p{{color:var(--muted);font-size:15px}}

  .adv-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}}
  .adv{{display:flex;gap:14px;align-items:flex-start;background:var(--soft);
       border:1px solid var(--line);border-radius:14px;padding:18px 20px;font-weight:500}}
  .adv-ic{{flex:0 0 26px;height:26px;border-radius:50%;display:grid;place-items:center;
         background:linear-gradient(135deg,var(--c1),var(--c2));color:#fff;font-size:14px;font-weight:800}}

  .cta{{position:relative;overflow:hidden;text-align:center;color:#fff;
       background:linear-gradient(135deg,var(--c1),var(--c2))}}
  .cta:after{{content:"";position:absolute;left:-120px;top:-120px;width:360px;height:360px;
            border-radius:50%;background:rgba(255,255,255,.10)}}
  .cta .wrap{{position:relative}}
  .cta h2{{color:#fff}}
  .cta p{{opacity:.92;font-size:17px;max-width:560px;margin:12px auto 30px}}
  .cta-row.center{{justify-content:center}}

  footer{{background:#0b1120;color:#93a0b4;padding:40px 0;text-align:center;font-size:14px}}
  footer .fb{{color:#fff;font-weight:800;font-size:18px;margin-bottom:6px}}
  footer a{{color:#cbd5e1;text-decoration:none}}
  @media(max-width:640px){{
    section{{padding:52px 0}} .hero{{padding:80px 0 84px}}
    .nav-phone{{display:none}} .cta-row .btn{{flex:1 1 100%;justify-content:center}}
  }}
</style></head><body>

<nav class="nav"><div class="wrap">
  <div class="brand"><span class="dot"></span>{title}</div>
  <div class="nav-r">{nav_phone}
    <a class="btn btn-wa btn-sm" href="{wa_href}">WhatsApp</a>
  </div>
</div></nav>

<header class="hero"><div class="wrap">
  <span class="eyebrow">{eyebrow}</span>
  <h1>{esc(content.get('hero_title'))}</h1>
  <p class="sub">{esc(content.get('hero_subtitle'))}</p>
  <div class="cta-row">
    <a class="btn btn-wa" href="{wa_href}">Написать в WhatsApp</a>
    {hero_call}
  </div>
</div></header>

<section class="about"><div class="wrap">
  <div class="sec-eyebrow">О нас</div>
  <h2>Коротко о главном</h2>
  <p>{esc(content.get('about'))}</p>
</div></section>

<section><div class="wrap">
  <div class="sec-eyebrow">Услуги</div>
  <h2>Что мы предлагаем</h2>
  <p class="lead">Полный спектр услуг под ваши задачи — с понятным результатом.</p>
  <div class="grid">{services_html}</div>
</div></section>

<section style="background:var(--soft)"><div class="wrap">
  <div class="sec-eyebrow">Почему мы</div>
  <h2>Почему выбирают нас</h2>
  <div class="adv-grid">{adv_html}</div>
</div></section>

<section class="cta" id="contact"><div class="wrap">
  <h2>{cta}</h2>
  <p>Напишите нам в WhatsApp — ответим быстро и подскажем по вашему вопросу.</p>
  <div class="cta-row center">
    <a class="btn btn-wa" href="{wa_href}">Написать в WhatsApp</a>
    {contact_call}
  </div>
</div></section>

<footer>
  <div class="fb">{title}</div>
  <div>{footer_meta}</div>
</footer>
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
