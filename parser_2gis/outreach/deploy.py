from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from ..logger import logger
from . import sitegen
from .options import OutreachOptions


def deploy_site(slug: str, opts: OutreachOptions,
                src_root: Optional[Path] = None) -> dict[str, Any]:
    """Publish a generated site by copying it into the Nginx-served directory.

    The whole platform runs on the VPS, so "deploy" is a local copy from the
    generator's output dir into ``opts.sites_dir/<slug>/``. Nginx (configured
    with a wildcard vhost) then serves it at ``<slug>.<base_domain>`` — no
    reload needed. See SETUP.md for the one-time Nginx/DNS/SSL setup.

    Returns {'slug', 'path', 'url'}. Raises RuntimeError with a friendly
    message on a missing source or an unwritable target.
    """
    src = (src_root or sitegen.local_sites_dir()) / slug
    if not (src / 'index.html').exists():
        raise RuntimeError('Сайт не найден — сначала сгенерируйте его (шаг 3).')

    if not opts.sites_dir:
        raise RuntimeError('Не задан каталог сайтов (sites_dir).')

    dest = Path(opts.sites_dir) / slug
    try:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, dirs_exist_ok=True)
    except PermissionError as e:
        raise RuntimeError(f'Нет прав на запись в {opts.sites_dir}. '
                           f'Проверьте владельца каталога на VPS.') from e
    except OSError as e:
        raise RuntimeError(f'Не удалось скопировать сайт: {e}') from e

    url = None
    if opts.base_domain:
        scheme = 'https' if opts.use_https else 'http'
        url = f'{scheme}://{slug}.{opts.base_domain}'

    logger.info('Сайт опубликован: %s -> %s (%s)', slug, dest, url or 'без домена')
    return {'slug': slug, 'path': str(dest), 'url': url}
