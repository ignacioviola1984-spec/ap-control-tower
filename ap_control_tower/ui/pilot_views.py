"""API estable para las páginas del producto unificado."""

from .pilot_pages_documents import render_documents, render_home, render_intake
from .pilot_pages_reporting import render_audit, render_indicators
from .pilot_pages_workflow import render_human_review, render_payment_proposal

__all__ = [
    "render_home",
    "render_intake",
    "render_documents",
    "render_human_review",
    "render_payment_proposal",
    "render_audit",
    "render_indicators",
]
