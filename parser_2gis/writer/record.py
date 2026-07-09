from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError

from ..logger import logger
from .models import CatalogItem

# Type fallback names for non-firm objects.
TYPE_NAMES = {
    'parking': 'Парковка', 'street': 'Улица', 'road': 'Дорога',
    'crossroad': 'Перекрёсток', 'station': 'Остановка',
}


def _extract_logo(item: dict[str, Any]) -> Optional[str]:
    """Best-effort: pull a business photo/avatar URL from the raw 2GIS item."""

    def first_url(value: Any) -> Optional[str]:
        if isinstance(value, str):
            return value if value.startswith('http') else None
        if isinstance(value, list):
            for el in value:
                hit = first_url(el)
                if hit:
                    return hit
        if isinstance(value, dict):
            direct_keys = (
                'avatar_url', 'logo_url', 'main_photo_url', 'preview_url',
                'photo_url', 'image_url', 'url',
            )
            for key in direct_keys:
                hit = first_url(value.get(key))
                if hit:
                    return hit
            nested_keys = (
                'avatar', 'logo', 'main_photo', 'photo', 'photos', 'images',
                'items', 'preview_urls',
            )
            for key in nested_keys:
                hit = first_url(value.get(key))
                if hit:
                    return hit
        return None

    for key in (
        'avatar', 'avatar_url', 'logo', 'logo_url', 'main_photo',
        'main_photo_url', 'external_content', 'photos', 'photo', 'images',
    ):
        hit = first_url(item.get(key))
        if hit:
            return hit
    return None


def extract_record(catalog_doc: Any) -> Optional[dict[str, Any]]:
    """Extract a flat, presentation-ready record from a Catalog Item document.

    Shared by the HTML writer and the web dashboard. Returns `None` for
    malformed documents or entries without a name.
    """
    try:
        item = catalog_doc['result']['items'][0]
    except (KeyError, IndexError, TypeError):
        return None

    try:
        catalog_item = CatalogItem(**item)
    except ValidationError as e:
        logger.error('Ошибка извлечения записи: %s', e.errors()[0].get('loc') if e.errors() else e)
        return None

    # Name / description
    name, description = None, None
    if catalog_item.name_ex:
        name = catalog_item.name_ex.primary
        description = catalog_item.name_ex.extension
    elif catalog_item.name:
        name = catalog_item.name
    elif catalog_item.type in TYPE_NAMES:
        name = TYPE_NAMES[catalog_item.type]
    if not name:
        return None

    city = None
    for div in catalog_item.adm_div:
        if div.type == 'city':
            city = div.name

    rating = review_count = None
    if catalog_item.reviews:
        rating = catalog_item.reviews.general_rating
        review_count = catalog_item.reviews.general_review_count

    # Contacts: keep the first value of each type.
    contacts: dict[str, str] = {}
    for group in catalog_item.contact_groups:
        for contact in group.contacts:
            if contact.type in contacts:
                continue
            if contact.type == 'phone':
                contacts['phone'] = contact.text or contact.value
            elif contact.type == 'email':
                contacts['email'] = contact.value
            elif contact.url:
                contacts[contact.type] = contact.url.split('?')[0]

    return {
        'name': name,
        'description': description,
        'rubrics': [r.name for r in catalog_item.rubrics],
        'address': catalog_item.address_name,
        'city': city,
        'rating': rating,
        'review_count': review_count,
        'contacts': contacts,
        'url': catalog_item.url,
        'logo_url': _extract_logo(item),
    }
