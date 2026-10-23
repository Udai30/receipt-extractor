"""Hugging Face Space entry point (Gradio SDK): ``uv run python app.py`` locally.

On the Space the package is not pip-installed (see docs/DEPLOY.md: installing it would make pip
re-resolve Gradio's Pillow cap against this project's Pillow 13 security pin), so ``src/`` is put
on the import path when the package is not importable.
"""

import sys
from pathlib import Path

try:
    import receipt_extractor  # noqa: F401
except ImportError:  # running from the Space checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from receipt_extractor.ui import launch

if __name__ == "__main__":
    launch()
