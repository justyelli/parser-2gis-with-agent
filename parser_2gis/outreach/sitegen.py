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

    # Hero side card: top-3 real advantages as a trust panel + contact line.
    hc_list = ''.join(f'<li>{esc(a)}</li>' for a in advantages[:3])
    hc_list_html = f'<ul class="hc-list">{hc_list}</ul>' if hc_list else ''
    hc_foot_parts = []
    if phone_e:
        hc_foot_parts.append(f'<a href="{tel_href}">{phone_e}</a>')
    if city_e:
        hc_foot_parts.append(f'<span>{city_e}</span>')
    hc_foot_html = (f'<div class="hc-foot">{"".join(hc_foot_parts)}</div>'
                    if hc_foot_parts else '')

    footer_meta = ' · '.join(x for x in (city_e, phone_e) if x)
    nav_phone = (f'<a class="nav-phone" href="{tel_href}">{phone_e}</a>'
                 if phone_e else '')
    hero_call = (f'<a class="btn btn-outline" href="{tel_href}">Позвонить</a>'
                 if tel_href else '')
    contact_call = (f'<a class="btn btn-glass" href="{tel_href}">{phone_e}</a>'
                    if phone_e else '')

    return f'''<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}{where}</title>
<meta name="description" content="{esc(content.get('hero_subtitle'))}">
<style>
  :root{{--c1:{c1};--c2:{c2};--ink:#121826;--body:#3c4658;--muted:#6a7488;
        --line:#e6e9f1;--surface:#f5f6fb;--tint:{c1}14;--wa:#25D366}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  html{{scroll-behavior:smooth}}
  body{{font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
       color:var(--body);line-height:1.65;background:#fff;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1120px;margin:0 auto;padding:0 24px}}
  a{{color:inherit}}
  h1,h2,h3{{color:var(--ink);text-wrap:balance}}
  a:focus-visible,.btn:focus-visible{{outline:3px solid {c1}66;outline-offset:3px;border-radius:6px}}

  .btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;font-weight:700;
       text-decoration:none;padding:14px 26px;border-radius:12px;font-size:16px;border:0;cursor:pointer;
       transition:transform .16s ease,box-shadow .16s ease,background .16s}}
  .btn:hover{{transform:translateY(-2px)}}
  .btn-wa{{background:var(--wa);color:#fff;box-shadow:0 12px 28px rgba(37,211,102,.34)}}
  .btn-outline{{background:transparent;color:var(--c1);border:1.5px solid {c1}55}}
  .btn-outline:hover{{background:var(--tint)}}
  .btn-glass{{background:rgba(255,255,255,.16);color:#fff;border:1px solid rgba(255,255,255,.55)}}
  .btn-sm{{padding:10px 18px;font-size:14px;border-radius:10px}}
  .cta-row{{display:flex;flex-wrap:wrap;gap:14px}}

  .nav{{position:sticky;top:0;z-index:50;background:rgba(255,255,255,.85);
       backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}}
  .nav .wrap{{display:flex;align-items:center;justify-content:space-between;height:68px}}
  .brand{{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;
         letter-spacing:-.3px;color:var(--ink)}}
  .brand .dot{{width:12px;height:12px;border-radius:4px;transform:rotate(45deg);
             background:linear-gradient(135deg,var(--c1),var(--c2))}}
  .nav-r{{display:flex;align-items:center;gap:18px}}
  .nav-phone{{text-decoration:none;font-weight:700;font-size:15px;color:var(--ink);white-space:nowrap}}

  .hero{{position:relative;overflow:hidden;padding:84px 0 88px;
        background:radial-gradient(780px 400px at 94% -12%,{c1}22,transparent 62%),
                   radial-gradient(640px 340px at -10% 128%,{c1}12,transparent 60%),#fff}}
  .hero-grid{{display:grid;grid-template-columns:1.12fr .88fr;gap:52px;align-items:center}}
  .h-eyebrow{{display:inline-flex;align-items:center;gap:10px;color:var(--c1);font-weight:700;
            font-size:13px;letter-spacing:1.6px;text-transform:uppercase;margin-bottom:20px}}
  .h-eyebrow:before{{content:"";width:28px;height:2px;background:var(--c1)}}
  .hero h1{{font-size:clamp(34px,5.2vw,58px);font-weight:800;line-height:1.05;
          letter-spacing:-.03em;max-width:15ch}}
  .hero .sub{{font-size:clamp(17px,1.9vw,20px);color:var(--body);margin:20px 0 32px;max-width:46ch}}

  .hcard{{position:relative;overflow:hidden;color:#fff;border-radius:22px;padding:32px 30px;
         background:linear-gradient(150deg,var(--c1),var(--c2));box-shadow:0 26px 60px {c1}3a}}
  .hcard:before{{content:"";position:absolute;right:-70px;top:-70px;width:220px;height:220px;
               border-radius:50%;background:rgba(255,255,255,.14)}}
  .hcard>*{{position:relative}}
  .hc-h{{font-weight:800;font-size:18px;letter-spacing:-.2px;margin-bottom:18px}}
  .hc-list{{list-style:none;display:grid;gap:13px}}
  .hc-list li{{position:relative;padding-left:32px;font-weight:500;font-size:15px;line-height:1.45}}
  .hc-list li:before{{content:"✓";position:absolute;left:0;top:1px;width:21px;height:21px;
                    border-radius:7px;background:rgba(255,255,255,.22);display:grid;
                    place-items:center;font-size:12px;font-weight:800}}
  .hc-foot{{margin-top:24px;padding-top:18px;border-top:1px solid rgba(255,255,255,.28);
          display:flex;flex-direction:column;gap:3px}}
  .hc-foot a{{font-weight:800;font-size:19px;text-decoration:none;letter-spacing:-.2px}}
  .hc-foot span{{opacity:.85;font-size:14px}}

  @keyframes up{{from{{opacity:0;transform:translateY(18px)}}to{{opacity:1;transform:none}}}}
  .hero-text>*{{animation:up .6s ease both}}
  .hero-text>*:nth-child(2){{animation-delay:.06s}}
  .hero-text>*:nth-child(3){{animation-delay:.12s}}
  .hero-text>*:nth-child(4){{animation-delay:.18s}}
  .hcard{{animation:up .7s ease .12s both}}

  section{{padding:78px 0}}
  .eyebrow2{{color:var(--c1);font-weight:700;font-size:13px;letter-spacing:1.6px;text-transform:uppercase}}
  h2{{font-size:clamp(27px,3.2vw,38px);font-weight:800;letter-spacing:-.025em;
     line-height:1.12;margin:11px 0 12px}}
  .lead{{color:var(--muted);font-size:17px;max-width:60ch;margin-bottom:40px}}

  .about-grid{{display:grid;grid-template-columns:.85fr 1.15fr;gap:48px;align-items:start}}
  .about-grid .head h2{{margin-top:0}}
  .about-grid p{{font-size:19px;line-height:1.72;color:var(--body)}}

  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px}}
  .card{{border:1px solid var(--line);border-radius:16px;padding:28px 26px;background:#fff;
        transition:transform .18s ease,box-shadow .18s ease,border-color .18s}}
  .card:hover{{transform:translateY(-5px);box-shadow:0 20px 46px rgba(18,24,38,.09);border-color:transparent}}
  .card h3{{font-size:18px;font-weight:700;letter-spacing:-.01em}}
  .card h3:after{{content:"";display:block;width:30px;height:3px;border-radius:2px;margin:13px 0 3px;
                background:linear-gradient(90deg,var(--c1),var(--c2))}}
  .card p{{color:var(--muted);font-size:15px;margin-top:10px}}

  .why{{background:var(--surface)}}
  .adv-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));
           gap:2px 34px;margin-top:6px}}
  .adv{{display:flex;gap:15px;align-items:center;padding:18px 2px;
       border-bottom:1px solid var(--line);font-weight:600;color:var(--ink);font-size:16px}}
  .adv-ic{{flex:0 0 32px;height:32px;border-radius:9px;display:grid;place-items:center;
         background:var(--tint);color:var(--c1);font-size:15px;font-weight:800}}

  .cta{{position:relative;overflow:hidden;text-align:center;color:#fff;
       background:linear-gradient(150deg,var(--c1),var(--c2))}}
  .cta:before{{content:"";position:absolute;left:-140px;top:-120px;width:360px;height:360px;
             border-radius:50%;background:rgba(255,255,255,.10)}}
  .cta:after{{content:"";position:absolute;right:-120px;bottom:-160px;width:360px;height:360px;
            border-radius:50%;background:rgba(255,255,255,.08)}}
  .cta .wrap{{position:relative}}
  .cta h2{{color:#fff}}
  .cta p{{opacity:.94;font-size:18px;max-width:52ch;margin:14px auto 32px}}
  .cta-row.center{{justify-content:center}}

  footer{{background:var(--ink);color:#9aa4b6;padding:44px 0;text-align:center;font-size:14px}}
  footer .fb{{color:#fff;font-weight:800;font-size:19px;letter-spacing:-.3px;margin-bottom:6px}}

  @media(max-width:860px){{
    .hero-grid,.about-grid{{grid-template-columns:1fr;gap:34px}}
    .hero{{padding:64px 0 68px}}
  }}
  @media(max-width:640px){{
    section{{padding:56px 0}} .nav-phone{{display:none}}
    .cta-row .btn{{flex:1 1 100%}} .adv-grid{{gap:0 20px}}
  }}
  @media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style></head><body>

<nav class="nav"><div class="wrap">
  <div class="brand"><span class="dot"></span>{title}</div>
  <div class="nav-r">{nav_phone}
    <a class="btn btn-wa btn-sm" href="{wa_href}">WhatsApp</a>
  </div>
</div></nav>

<header class="hero"><div class="wrap hero-grid">
  <div class="hero-text">
    <span class="h-eyebrow">{eyebrow}</span>
    <h1>{esc(content.get('hero_title'))}</h1>
    <p class="sub">{esc(content.get('hero_subtitle'))}</p>
    <div class="cta-row">
      <a class="btn btn-wa" href="{wa_href}">Написать в WhatsApp</a>
      {hero_call}
    </div>
  </div>
  <aside class="hcard">
    <div class="hc-h">Почему выбирают нас</div>
    {hc_list_html}
    {hc_foot_html}
  </aside>
</div></header>

<section class="about"><div class="wrap about-grid">
  <div class="head">
    <div class="eyebrow2">О нас</div>
    <h2>Коротко о главном</h2>
  </div>
  <p>{esc(content.get('about'))}</p>
</div></section>

<section><div class="wrap">
  <div class="eyebrow2">Услуги</div>
  <h2>Что мы предлагаем</h2>
  <p class="lead">Полный спектр услуг под ваши задачи — с понятным результатом.</p>
  <div class="grid">{services_html}</div>
</div></section>

<section class="why"><div class="wrap">
  <div class="eyebrow2">Почему мы</div>
  <h2>Причины обратиться именно к нам</h2>
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
