from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OutreachOptions(BaseModel):
    """Settings for the lead-generation / outreach platform.

    Holds only non-secret configuration. Secrets (e.g. the GLM API key)
    are read from the environment at runtime, never stored in the config file:
        * GLM_API_KEY - key for the site generator (GLM / Z.ai API).
        * GLM_BASE_URL - optional override of the OpenAI-compatible endpoint.

    Attributes:
        enabled: Master switch for the outreach features in the dashboard.
        base_domain: Wildcard domain that serves generated sites, e.g. "mysites.ru".
            A generated site for slug "cafe-almaty" is served at
            "cafe-almaty.mysites.ru".
        sites_dir: Directory Nginx serves subdomains from (root of "/var/www/sites"
            style layout). Each site lives in "<sites_dir>/<slug>/".
        use_https: Whether generated links use https (requires a wildcard cert).
        llm_model: GLM model id used by the site generator.
        gateway_url: Base URL of the Node WhatsApp gateway (Baileys), e.g.
            "http://127.0.0.1:8667".
        send_daily_limit: Max WhatsApp messages to send per day (anti-ban).
        send_delay_min: Minimum delay between messages, seconds (anti-ban).
        send_delay_max: Maximum delay between messages, seconds (anti-ban).
        send_hours_start: Earliest hour (0-23, local) messages may be sent.
        send_hours_end: Latest hour (0-23, local) messages may be sent.
    """
    model_config = ConfigDict(validate_assignment=True)

    enabled: bool = False
    base_domain: str = ''
    sites_dir: str = '/var/www/sites'
    use_https: bool = True
    llm_model: str = 'glm-5'
    gateway_url: str = 'http://127.0.0.1:8667'
    send_daily_limit: int = Field(40, ge=1, le=10000)
    send_delay_min: int = Field(40, ge=1)
    send_delay_max: int = Field(120, ge=1)
    send_hours_start: int = Field(9, ge=0, le=23)
    send_hours_end: int = Field(21, ge=0, le=23)
