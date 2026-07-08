from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from ..logger import logger

# OpenAI-compatible endpoint for GLM (Z.ai, international). Override with
# GLM_BASE_URL, e.g. https://open.bigmodel.cn/api/paas/v4/ for Zhipu (CN).
GLM_DEFAULT_BASE_URL = 'https://api.z.ai/api/paas/v4/'

# Fallback chain: if the configured model id isn't available on the account
# (e.g. "glm-5" not enabled yet), calls transparently drop to the next id.
GLM_FALLBACK_MODELS = ['glm-5', 'glm-4.6', 'glm-4.5', 'glm-4-plus', 'glm-4']


def parse_json(text: str) -> Any:
    """Parse model JSON output, tolerating ```json … ``` code fences."""
    s = text.strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s)
        s = re.sub(r'\s*```$', '', s)
    return json.loads(s)


def make_client():
    """Build an OpenAI-compatible client pointed at GLM.

    Requires the `openai` package and the GLM_API_KEY env var; GLM_BASE_URL
    overrides the endpoint. Raises RuntimeError with a friendly message.
    """
    api_key = os.getenv('GLM_API_KEY')
    if not api_key or api_key == 'ВСТАВЬ_КЛЮЧ_СЮДА':
        raise RuntimeError('Не задан GLM_API_KEY (ключ GLM / Z.ai API) — впишите его в .env.')
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError('Не установлен пакет openai (pip install openai).') from e
    base_url = os.getenv('GLM_BASE_URL', GLM_DEFAULT_BASE_URL)
    return OpenAI(api_key=api_key, base_url=base_url)


def models_for(model: str) -> list[str]:
    """The configured model first, then the rest of the fallback chain."""
    return [model] + [m for m in GLM_FALLBACK_MODELS if m != model]


def complete(client, models: list[str], messages: list[dict], *,
             max_tokens: int, json_mode: bool = True,
             temperature: Optional[float] = None) -> str:
    """Run a GLM chat completion, tolerating model/endpoint quirks.

    Tries each model id in order (skipping ones that don't exist) and, when
    json_mode is on, first asks for JSON then retries plain if the endpoint
    rejects ``response_format``. Auth/connection errors propagate unchanged so
    the real cause surfaces. Returns the response text; raises RuntimeError if
    nothing worked.
    """
    from openai import BadRequestError, NotFoundError

    last_err: Optional[Exception] = None
    for model in models:
        json_options = (True, False) if json_mode else (False,)
        for use_json in json_options:
            kwargs: dict[str, Any] = {
                'model': model, 'max_tokens': max_tokens, 'messages': messages,
            }
            if use_json:
                kwargs['response_format'] = {'type': 'json_object'}
            if temperature is not None:
                kwargs['temperature'] = temperature
            try:
                resp = client.chat.completions.create(**kwargs)
                text = resp.choices[0].message.content if resp.choices else None
                if text:
                    logger.info('GLM: ответ от модели %s', model)
                    return text
                last_err = RuntimeError('пустой ответ')
            except NotFoundError as e:      # model id doesn't exist -> next model
                last_err = e
                break
            except BadRequestError as e:    # maybe json mode unsupported -> retry plain
                last_err = e
                continue
    raise RuntimeError(
        f'GLM API не ответил ни на одну из моделей ({", ".join(models)}): {last_err}'
    )
