"""API estable para las páginas del producto unificado."""

from .agent_admin import render_agent_admin
from .command_center import render_home
from .pilot_pages_documents import render_documents, render_intake
from .pilot_pages_reporting import render_audit, render_indicators
from .pilot_pages_workflow import render_human_review, render_payment_proposal
from .vendor_intake import render_new_vendor

__all__ = [
    "render_home",
    "render_intake",
    "render_new_vendor",
    "render_documents",
    "render_human_review",
    "render_payment_proposal",
    "render_audit",
    "render_indicators",
    "render_agent_admin",
]
