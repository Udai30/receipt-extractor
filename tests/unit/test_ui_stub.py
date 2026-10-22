"""The UI stub imports cleanly and says where the real app comes from."""

from __future__ import annotations

import pytest

from receipt_extractor.ui import launch


def test_launch_is_a_stub_until_m3_2() -> None:
    with pytest.raises(NotImplementedError, match=r"M3\.2"):
        launch()
