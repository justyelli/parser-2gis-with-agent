from pathlib import Path

from parser_2gis.outreach import db, sitegen
from parser_2gis.outreach.campaign import CampaignRunner
from parser_2gis.outreach.options import OutreachOptions


def _content():
    return {
        'business_type': 'Dentistry',
        'hero_title': 'Healthy smile without delays',
        'hero_subtitle': 'Book a visit and get a clear plan.',
        'about': 'We help patients choose the right treatment plan.',
        'services': [{'title': 'Check-up', 'description': 'Fast diagnostics.'}],
        'advantages': ['Clear consultation', 'Convenient location'],
        'cta_text': 'Write to us',
    }


def test_build_site_creates_unique_client_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(sitegen, 'generate_site_content',
                        lambda niche, city, model: _content())
    leads = [
        {'id': 1, 'name': 'Avenia', 'phone': '+7 700 111 11 11', 'city': 'Almaty'},
        {'id': 2, 'name': 'Avenia', 'phone': '+7 700 222 22 22', 'city': 'Almaty'},
    ]

    info = sitegen.build_site('Private dentists', 'Almaty', model='test',
                              leads=leads, out_root=tmp_path)

    root = Path(info['dir'])
    assert [c['slug'] for c in info['clients']] == ['avenia', 'avenia-2']
    assert (root / 'avenia' / 'index.html').exists()
    assert (root / 'avenia-2' / 'index.html').exists()
    assert '+7 700 111 11 11' in (root / 'avenia' / 'index.html').read_text(encoding='utf-8')
    assert '+7 700 222 22 22' in (root / 'avenia-2' / 'index.html').read_text(encoding='utf-8')


def test_campaign_uses_matching_personal_links(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'default_db_path', lambda: tmp_path / 'outreach.db')
    with db.session() as conn:
        db.upsert_lead(conn, name='Avenia', phone='+7 700 111 11 11',
                       phone_wa='77001111111', city='Almaty',
                       niche='Private dentists')
        db.upsert_lead(conn, name='Avenia', phone='+7 700 222 22 22',
                       phone_wa='77002222222', city='Almaty',
                       niche='Private dentists')

    opts = OutreachOptions(base_domain='justmysite.site', use_https=True)
    runner = CampaignRunner()
    runner._run(opts, 'http://127.0.0.1:1', niche='Private dentists',
                city='Almaty', message_template='{link}', link=None,
                dry_run=True)

    with db.session() as conn:
        rows = conn.execute('SELECT text FROM messages ORDER BY id').fetchall()

    assert [r['text'] for r in rows] == [
        'https://private-dentists-almaty.justmysite.site/avenia',
        'https://private-dentists-almaty.justmysite.site/avenia-2',
    ]


def test_upsert_lead_refreshes_logo_url(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'default_db_path', lambda: tmp_path / 'outreach.db')
    with db.session() as conn:
        first_id = db.upsert_lead(conn, name='Avenia',
                                  phone='+7 700 111 11 11',
                                  phone_wa='77001111111',
                                  city='Almaty',
                                  niche='Private dentists')
        second_id = db.upsert_lead(conn, name='Avenia',
                                   phone='+7 700 111 11 11',
                                   phone_wa='77001111111',
                                   city='Almaty',
                                   niche='Private dentists',
                                   logo_url='https://img.2gis.test/logo.jpg')
        row = conn.execute('SELECT logo_url FROM leads WHERE id = ?',
                           (first_id,)).fetchone()

    assert second_id == first_id
    assert row['logo_url'] == 'https://img.2gis.test/logo.jpg'
