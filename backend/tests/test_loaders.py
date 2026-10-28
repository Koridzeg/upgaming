"""Loaders, tested on the real synthetic corpus plus small inline edge cases."""

import pytest

from investigator.datasets.synthetic import CORPUS_DIR
from investigator.ingestion.loaders import load_document
from investigator.ingestion.models import DocumentParseError, UnsupportedFormatError


def load(name: str):
    return load_document(name, (CORPUS_DIR / name).read_bytes())


def texts(document, kind):
    return [block.text for block in document.blocks if block.kind == kind]


@pytest.mark.parametrize("path", sorted(CORPUS_DIR.iterdir()), ids=lambda p: p.name)
def test_every_corpus_file_loads_with_title_date_and_blocks(path):
    document = load_document(path.name, path.read_bytes())

    assert document.title
    assert document.blocks
    assert document.metadata["source_filename"] == path.name
    if path.suffix != ".csv":  # the alert export carries no date of its own
        assert document.metadata.get("created_at"), "every other document is dated"


def test_pdf_recovers_metadata_headings_paragraphs_and_tables():
    document = load("postmortem-INC-2026-0311.pdf")

    assert document.title == "Postmortem INC-2026-0311 - Card payment processing delays"
    assert document.metadata["author"] == "Priya Nair, Daniel Okafor"
    assert document.metadata["created_at"] == "2026-03-18T00:00:00"
    assert document.metadata["page_count"] == 2

    headings = [(b.text, b.level) for b in document.blocks if b.kind == "heading"]
    assert headings[0][1] == 1
    assert ("Root cause", 2) in headings

    # Lines wrapped at the margin are joined back into one paragraph...
    summary = next(t for t in texts(document, "paragraph") if t.startswith("Between 13:52"))
    assert summary.endswith("Neither change caused problems on its own.")
    assert "\n" not in summary
    # ...while short, deliberately broken lines keep their line breaks.
    assert "\nDate: 2026-03-18\n" in texts(document, "paragraph")[0]

    # A table cell that wraps over several lines stays in one self-describing row.
    rows = texts(document, "table_row")
    assert (
        "Service: merchant-api; Effect: Checkout requests timed out after 30 seconds (HTTP 504)"
        in rows
    )
    assert any(
        r.startswith("ID: AI-4;") and "CHG-2041 has no approver on record" in r for r in rows
    )
    assert {b.page for b in document.blocks} == {1, 2}


def test_docx_reads_styles_and_core_properties():
    document = load("postmortem-INC-2026-0311-draft.docx")

    assert document.metadata["author"] == "Priya Nair"
    assert document.metadata["created_at"].startswith("2026-03-14")
    assert document.metadata["keywords"] == "status: draft"
    assert "Open questions" in texts(document, "heading")
    assert "Approximately 11,900 card payments delayed by more than 30 seconds." in texts(
        document, "list_item"
    )


def test_html_drops_hidden_text_and_page_chrome_but_keeps_visible_content():
    advisory = load("vendor-advisory-orchard-sdk.html")
    status = load("status-page-history.html")

    assert advisory.metadata["hidden_elements_removed"] == 1
    assert "All systems nominal" not in advisory.text
    # The visible injection is real page content; defending against it is the agent's job.
    assert "Ignore all previous instructions" in advisory.text

    assert "Subscribe" not in status.text  # nav and footer
    assert "statusPageVersion" not in status.text  # script
    assert "Between 14:05 and 16:40 UTC" in status.text


def test_json_records_are_found_and_nulls_kept():
    document = load("change-log-2026-q1.json")

    assert document.metadata["records_path"] == "$.changes"
    assert document.metadata["record_count"] == 11
    assert document.metadata["created_at"] == "2026-04-01T08:00:00Z"
    chg_2041 = next(t for t in texts(document, "record") if t.startswith("id: CHG-2041;"))
    assert "approver: null" in chg_2041  # "no approver recorded" must survive loading


def test_csv_rows_become_self_describing_records():
    document = load("alerts-2026-03.csv")

    assert document.metadata["row_count"] == 18
    assert texts(document, "record")[0].startswith(
        "timestamp_utc: 2026-03-03T08:14:00Z; alert_id: ALR-88101; service: ledger-service"
    )


def test_markdown_front_matter_becomes_metadata():
    document = load("runbook-payment-gateway.md")

    assert document.title == "Runbook - payment-gateway"
    assert document.metadata["author"] == "Priya Nair"
    assert document.metadata["created_at"] == "2026-02-02"
    assert any("deployctl rollback payment-gateway" in t for t in texts(document, "table_row"))


def test_csv_with_bom_and_semicolons():
    data = "﻿name;team\nAna;Risk\n".encode()

    document = load_document("people.csv", data)

    assert texts(document, "record") == ["name: Ana; team: Risk"]


def test_text_falls_back_to_latin1_and_records_it():
    document = load_document("notes.txt", "Caf\xe9 opened.\n\nSecond paragraph.".encode("latin-1"))

    assert texts(document, "paragraph") == ["Café opened.", "Second paragraph."]
    assert document.metadata["encoding"] == "latin-1"


def test_jsonl_lines_become_records():
    document = load_document("events.jsonl", b'{"id": 1, "ok": true}\n\n{"id": 2, "ok": null}\n')

    assert texts(document, "record") == ["id: 1; ok: true", "id: 2; ok: null"]


def test_unsupported_extension_is_rejected():
    with pytest.raises(UnsupportedFormatError, match="Supported: "):
        load_document("slides.pptx", b"PK...")


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("broken.pdf", b"this is not a pdf"),
        ("broken.docx", b"not a zip file"),
        ("broken.json", b"{not json"),
        ("empty.md", b""),
        ("blank.html", b"<html><body><script>x()</script></body></html>"),
    ],
)
def test_unreadable_files_raise_one_domain_error(name, data):
    with pytest.raises(DocumentParseError):
        load_document(name, data)
