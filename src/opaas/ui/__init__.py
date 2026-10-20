"""The local dashboard, `opaas ui` (D75, D79).

A server that reads the run store and launches `opaas` commands, and
measures nothing itself. Its design is `docs/dashboard.md`. Starlette, uvicorn
and Jinja2 come with the `ui` extra: nothing here is imported by the rest of
OPAAS, so the base install neither needs nor loads them.
"""
