"""The portfolio theme (UI_UX_DESIGN §S2): neutral slate, one accent, Inter / JetBrains Mono."""

from __future__ import annotations

from typing import Any

__all__ = ["CSS", "build_theme"]

# Badges carry text; colour only reinforces it (UI_UX_DESIGN §S4). Numbers right-aligned.
CSS = """
.badge p { font-weight: 600; }
.metadata p { font-family: var(--font-mono); font-size: 0.85rem; }
.banner p { padding: 0.5rem 0.75rem; border-left: 4px solid var(--color-accent); }
td:nth-child(n+2) { text-align: right; }
"""


def build_theme() -> Any:
    """A ``gr.themes.Base`` subclass instance (Gradio imported lazily)."""
    import gradio as gr  # noqa: PLC0415

    class ReceiptTheme(gr.themes.Base):
        def __init__(self) -> None:
            super().__init__(
                primary_hue=gr.themes.colors.indigo,
                secondary_hue=gr.themes.colors.slate,
                neutral_hue=gr.themes.colors.slate,
                radius_size=gr.themes.sizes.radius_md,
                font=(gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"),
                font_mono=(
                    gr.themes.GoogleFont("JetBrains Mono"),
                    "ui-monospace",
                    "Consolas",
                    "monospace",
                ),
            )

    return ReceiptTheme()
