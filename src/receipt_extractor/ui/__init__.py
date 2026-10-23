"""Gradio Blocks app and theme for the Hugging Face Space (TRD §S2, §S7, UI_UX_DESIGN).

Callbacks hold no business logic: ``reports`` (Results/Failures from ``reports/``),
``showcase`` (cached sample results), ``live`` (guarded pipeline A for uploads), ``views`` and
``presenters`` (formatting) are plain Python and tested without a browser. Gradio is imported
only when the app is built.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["launch"]


def launch(*, reports_dir: Path | None = None) -> None:
    """Start the demo app (``app.py`` / ``uv run python app.py``)."""
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    from receipt_extractor.ui.app import AppServices, build_app, launch_options  # noqa: PLC0415
    from receipt_extractor.ui.live import default_live_service  # noqa: PLC0415
    from receipt_extractor.ui.showcase import load_showcase  # noqa: PLC0415

    services = AppServices(
        reports_dir=reports_dir or Path("reports"),
        live=default_live_service(),
        showcase=load_showcase(),
    )
    build_app(services).launch(**launch_options())
