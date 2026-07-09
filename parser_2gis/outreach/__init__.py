"""Lead-generation / outreach platform.

Extends the 2GIS parser with: capturing "businesses without a website" as
leads, generating a template site per niche (GLM API), deploying it to a
subdomain, and sending the link over WhatsApp (via a Node/Baileys gateway).
"""
from __future__ import annotations

from . import db, deploy, sitegen
from .options import OutreachOptions
from .phone import to_wa_number


def __getattr__(name: str):
    if name == 'CampaignRunner':
        from .campaign import CampaignRunner
        return CampaignRunner
    if name == 'capture_leads':
        from .leads import capture_leads
        return capture_leads
    raise AttributeError(name)


__all__ = ['OutreachOptions', 'db', 'sitegen', 'deploy', 'capture_leads',
           'to_wa_number', 'CampaignRunner']
