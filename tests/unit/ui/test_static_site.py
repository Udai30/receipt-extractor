"""Static Space page: built from the same report files and showcase as the Gradio app."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from receipt_extractor.ui.reports import load_failures, load_results
from receipt_extractor.ui.showcase import SHOWCASE_DIR, load_showcase
from receipt_extractor.ui.static_site import ASSETS, INDEX, build_static_site, render_page

from .test_views_and_reports import row, write_results


def write_failures(reports: Path) -> None:
    with (reports / "failures.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["run_id", "case_id", "variant", "status", "score", "failure_label", "note", "fixed_by"]
        )
        writer.writerow(["r1", "test-0001", "A", "fail", "0.5", "ocr_misread", "<b>x</b>", ""])
        writer.writerow(["r1", "test-0002", "A", "fail", "0.0", "ocr_misread", "", ""])
        writer.writerow(["r2", "test-0003", "B", "fail", "0.8", "other", "", ""])


def test_build_writes_page_card_and_images(tmp_path: Path) -> None:
    reports, out = tmp_path / "reports", tmp_path / "site"
    write_results(reports, [row("A", "0.88", "0.300000"), row("B", "0.91", "0.900000")])
    write_failures(reports)
    (reports / "primary_metric.png").write_bytes(b"\x89PNG")
    card = tmp_path / "card.md"
    card.write_text("---\nsdk: static\n---\n", encoding="utf-8")

    written = build_static_site(reports, out, space_readme=card)

    page = (out / INDEX).read_text(encoding="utf-8")
    showcase = load_showcase()
    assert showcase is not None
    assert {p.name for p in written} >= {INDEX, "README.md", "primary_metric.png"}
    for case in showcase.cases:
        assert (out / ASSETS / case.image).is_file() and case.case_id in page
    assert "Gemini vision: 0.91 field F1 vs OCR + LLM 0.88 at 3.0x the cost" in page
    assert f'src="{ASSETS}/primary_metric.png"' in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page  # notes are escaped


def test_failure_summary_counts_by_cause_and_pipeline(tmp_path: Path) -> None:
    write_failures(tmp_path)
    page = render_page(load_results(tmp_path), None, load_failures(tmp_path))
    assert re.search(r"<td>ocr_misread</td><td class=num>2</td><td class=num>0</td>", page)
    assert re.search(r"<td>total</td><td class=num>2</td><td class=num>1</td>", page)
    assert page.count('data-cause="ocr_misread"') == 2


def test_empty_states_and_no_external_requests(tmp_path: Path) -> None:
    page = render_page(load_results(tmp_path), None, load_failures(tmp_path))
    assert "No results yet" in page and "No sample receipts bundled" in page
    assert "No failure list yet" in page
    full = render_page(load_results(tmp_path), load_showcase(SHOWCASE_DIR), load_failures(tmp_path))
    assert not re.search(r"""(src|href)=["']?(https?:)?//""", full)
