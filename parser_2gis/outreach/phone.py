from __future__ import annotations

import re
from typing import Optional

# Country codes we consider already "international" when they lead the number.
# ru/kz=7, kg=996, uz=998, by=375, az=994.
_KNOWN_CC = ('7', '375', '380', '994', '996', '998', '992', '993')


def to_wa_number(raw: Optional[str], default_cc: str = '7') -> Optional[str]:
    """Normalize a raw phone string to WhatsApp-ready digits (no '+').

    Handles the messy inputs 2GIS yields: "+7 700 111-22-33", "8 (700) ...",
    "https://wa.me/77001112233", etc. Returns digits only (e.g. "77001112233")
    or None if nothing plausible can be extracted.

    Args:
        raw: Any phone-like string or wa.me URL.
        default_cc: Country code prepended to a bare national number when no
            recognizable code is present (7 = Russia/Kazakhstan by default).
    """
    if not raw:
        return None

    digits = re.sub(r'\D', '', raw)
    if not digits:
        return None

    # Local trunk prefix "8XXXXXXXXXX" (11 digits) -> country code 7.
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]

    # Bare 10-digit national number -> prepend the default country code.
    if len(digits) == 10 and not digits.startswith(_KNOWN_CC):
        digits = default_cc + digits

    # WhatsApp accepts 8..15 digit E.164 numbers; be lenient but sane.
    if 10 <= len(digits) <= 15:
        return digits
    return None
