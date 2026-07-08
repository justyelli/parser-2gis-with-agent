"""Lead-generation / outreach platform.

Extends the 2GIS parser with: capturing "businesses without a website" as
leads, generating a template site per niche (GLM API), deploying it to a
subdomain, and sending the link over WhatsApp (via a Node/Baileys gateway).
"""
from __future__ import annotations

from . import db, deploy, sitegen
from .campaign import CampaignRunner
from .leads import capture_leads
from .options import OutreachOptions
from .phone import to_wa_number

__all__ = ['OutreachOptions', 'db', 'sitegen', 'deploy', 'capture_leads',
           'to_wa_number', 'CampaignRunner']
