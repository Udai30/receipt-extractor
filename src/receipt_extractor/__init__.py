"""Receipt-to-JSON Extractor: receipt photo in, validated ``Receipt`` JSON out.

Importing this package must stay side-effect free: no settings are loaded, no
datasets or model weights are fetched, and no network calls are made.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("receipt-extractor")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
