"""Thin server-rendered review UI for the HITL gate.

A FastAPI router mounted on the main app. Renders the existing
``RedlineReport`` via ``render_redline_html`` (iframed) and lets a
reviewer record an approval/revise/reject decision through the same
audit table the JSON ``POST /report/decision`` writes.

See ADR 0010 for the HITL gate design and ADR 0016 for the UI choice
(server-rendered, iframe over redline HTML, HTMX decision form).
"""

from adv_lens.app.web.routes import router

__all__ = ["router"]
