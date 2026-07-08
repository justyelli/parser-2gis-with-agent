from __future__ import annotations

from typing import Any, Optional

from . import llm

# The persona + hard rules for cold WhatsApp outreach. Kept deliberately strict
# so messages read like a real person wrote them one at a time — not a blast.
_SYSTEM = (
    'Ты — основатель небольшой веб-студии и лично пишешь владельцу малого '
    'бизнеса ПЕРВОЕ сообщение в WhatsApp. Ты уже сделал для него готовый сайт-'
    'визитку и хочешь, чтобы он открыл ссылку и посмотрел.\n'
    'Пиши как живой человек конкретному человеку. Жёсткие правила:\n'
    '— Одно сообщение, 2–4 коротких предложения, примерно 300–550 символов.\n'
    '— Естественно упомяни бизнес по названию (без «Уважаемый клиент», без '
    '«ООО»/«ИП» и юр. форм, без КАПСА).\n'
    '— Логика: заметил вас в 2ГИС по нише в вашем городе → увидел, что нет '
    'своего сайта → уже собрал готовый сайт-визитку → вот ссылка, посмотрите.\n'
    '— Дружелюбно и по-человечески, без канцелярита, штампов и продающего '
    'напора. Максимум 1–2 эмодзи (можно вообще без).\n'
    '— Ничего не выдумывай про их бизнес (не приписывай услуги, награды, '
    'адреса, которых не знаешь). Опирайся только на переданные данные.\n'
    '— Обязательно вставь ссылку РОВНО как передана, не меняй и не сокращай.\n'
    '— Один мягкий призыв в конце (посмотреть / написать, если понравится).\n'
    '— Каждое сообщение формулируй по-своему, не повторяй шаблонные фразы.\n'
    'Верни ТОЛЬКО JSON вида {"message": "<текст сообщения>"}.'
)


def build_prompt(lead: dict[str, Any], *, link: Optional[str], brief: str) -> str:
    """Compose the user-turn prompt describing one lead for personalization."""
    name = (lead.get('name') or '').strip() or 'бизнес без названия'
    parts = [f'Название бизнеса: {name}']
    if lead.get('niche'):
        parts.append(f'Ниша: {lead["niche"]}')
    if lead.get('city'):
        parts.append(f'Город: {lead["city"]}')
    if lead.get('address'):
        parts.append(f'Адрес: {lead["address"]}')
    parts.append(f'Ссылка на готовый сайт: {link or "(ссылка будет добавлена отдельно)"}')
    if brief and brief.strip():
        parts.append(
            '\nПожелания/оффер от отправителя (учти по смыслу, дословно не '
            f'копируй): {brief.strip()}'
        )
    parts.append('\nНапиши персональное сообщение именно этому бизнесу.')
    return '\n'.join(parts)


def generate_message(lead: dict[str, Any], *, link: Optional[str], brief: str,
                     model: str, client: Any = None,
                     temperature: float = 0.9) -> str:
    """Generate one personalized WhatsApp message for a single lead via GLM.

    Args:
        lead: dict with at least ``name`` (and optionally niche/city/address).
        link: the site URL to include verbatim in the message.
        brief: the sender's free-text offer/tone guidance (may be empty).
        model: preferred GLM model id (falls back per :func:`llm.models_for`).
        client: optional pre-built GLM client to reuse across a campaign.
        temperature: higher -> more varied wording between leads.

    Returns:
        The message text (link appended defensively if the model dropped it).

    Raises:
        RuntimeError: on missing key/package or if GLM returned nothing usable.
    """
    client = client or llm.make_client()
    messages = [
        {'role': 'system', 'content': _SYSTEM},
        {'role': 'user', 'content': build_prompt(lead, link=link, brief=brief)},
    ]
    text = llm.complete(client, llm.models_for(model), messages,
                        max_tokens=600, temperature=temperature)
    data = llm.parse_json(text)
    message = (data.get('message') if isinstance(data, dict) else None) or ''
    message = message.strip()
    if not message:
        raise RuntimeError('GLM вернул пустое сообщение.')
    # Safety net: make sure the link survived (models sometimes paraphrase it).
    if link and link not in message:
        message = f'{message}\n{link}'
    return message
