"""The transmittal registration screen: extract → validate → confirm → save.

Gemini itself cannot be reached from the test environment (and should not be), so the
provider call is replaced by ``_call_gemini``. Everything else is real: the PDF is
rasterised, the answer is normalised by the deterministic helpers, the validators run
against a real register, and the save goes through the real document/revision tables.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DocumentRevision, MDLItem, Transmittal, TransmittalLine
from app.services import metadata_extraction, registration
from app.services.metadata_extraction import normalise_answer, to_gregorian
from tests.conftest import auth_headers
from tests.test_processing_session import build_text_pdf

TITLE_BLOCK = [
    (40, 520, "Document No: 2102-20-ED-ME-DWG-4501"),
    (40, 500, "Title: Tank orientation"),
    (40, 480, "Discipline: MECHANICAL"),
    (40, 460, "Unit: 20"),
    (40, 440, "Revision: A0"),
    (40, 420, "P.O.I: IFR"),
]

ANSWER = {
    "doc_number": "2102-20-ED-ME-DWG-4501",
    "title": "Tank orientation For Tank (TK-20124 & TK-20123)",
    "revision": "A0",
    "revision_date": "1402.09.06",
    "purpose_of_issue": "IFR",
    "project_code": "2102",
    "unit": "20",
    "phase": "ED",
    "discipline": "ME",
    "doc_type": "DWG",
    "serial_no": "4501",
    "contract_no": "73-02-102/پ م ص",
    "total_pages": 4,
    "confidence": {"doc_number": 0.97, "title": 0.91, "revision": 0.95, "purpose_of_issue": 0.9},
    "conflicts": [],
}


@pytest.fixture()
def gemini(monkeypatch):
    """Replace the provider call; the payload/answer plumbing stays real."""
    state = {"answer": ANSWER, "calls": 0, "raise": None}

    def fake_call(image_png: bytes, timeout: int) -> dict:
        state["calls"] += 1
        if state["raise"] is not None:
            raise state["raise"]
        return json.loads(json.dumps(state["answer"]))

    monkeypatch.setattr(metadata_extraction, "_call_gemini", fake_call)
    monkeypatch.setattr(metadata_extraction, "_endpoint_trusted", lambda: True)
    monkeypatch.setattr(metadata_extraction.settings, "vlm_api_key", "test-key", raising=False)
    return state


def _client_key(client: TestClient, email: str = "dcc@edms-test.com") -> dict:
    return auth_headers(client, email)


def _upload(client: TestClient, project_id, headers, pdf: bytes, name="drawing.pdf"):
    return client.post(
        f"/api/v1/projects/{project_id}/registration/extract",
        headers=headers,
        files={"files": (name, pdf, "application/pdf")},
    )


def _newest(response) -> dict:
    """The row the last upload produced — the sheet also lists the earlier rows."""
    payload = response.json()
    assert response.status_code == 200, response.text
    return payload["rows"][-1]


def test_the_same_pdf_uploaded_twice_is_flagged_not_a_crash(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    pdf = build_text_pdf(TITLE_BLOCK)
    _upload(client, project.id, headers, pdf)
    second = _upload(client, project.id, headers, pdf)
    assert second.status_code == 200, second.text
    rows = second.json()["rows"]
    assert len(rows) == 2
    assert rows[-1]["validation_state"] == "duplicate"
    assert any("همین برگه" in message for message in rows[-1]["validation_messages"])
    assert client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={}).status_code == 422


# ------------------------------------------------------------------ normalising
def test_the_answer_is_normalised_not_trusted():
    raw = dict(ANSWER)
    raw["revision"] = "rev a0"
    raw["doc_number"] = "۲۱۰۲-۲۰-ED-ME-DWG-4501"
    raw["revision_date"] = "۱۴۰۲.۰۹.۰۶"
    out = normalise_answer(raw)
    assert out["data"]["doc_number"] == "2102-20-ED-ME-DWG-4501"
    assert out["data"]["revision"] == "A0"
    assert out["data"]["revision_date"] == "1402-09-06"  # as printed
    assert out["data"]["revision_date_gregorian"] == "2023-11-27"  # Jalali converted
    assert out["confidence"]["doc_number"] == 0.97


def test_gregorian_passthrough_and_bad_dates():
    assert to_gregorian("2024-07-22") == "2024-07-22"
    assert to_gregorian(None, "1403/05/01") == "2024-07-22"
    assert to_gregorian("not a date") is None


# -------------------------------------------------------------------- the API
def test_extract_creates_one_row_per_file(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    pdf = build_text_pdf(TITLE_BLOCK)
    response = _upload(client, project.id, headers, pdf, "2102-20-ED-ME-DWG-4501.pdf")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["doc_no"] == "2102-20-ED-ME-DWG-4501"
    assert row["title"].startswith("Tank orientation")
    assert row["version"] == "A0"
    assert row["poi"] == "IFR"
    assert row["discipline_code"] == "ME"
    assert row["discipline_label"] == "ME-Mechanical"  # from the editable table
    assert row["extraction_status"] == "ok"
    assert row["extraction_model"]
    # never taken from the model
    assert row["doc_status"] is None
    assert row["doc_percent"] == 0
    assert row["confidentiality"] == "Normal"
    assert row["validation_state"] == "new"
    assert row["file_url"].endswith(row["source_file_id"])


def test_two_files_give_two_rows(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    pdf = build_text_pdf(TITLE_BLOCK)
    other = build_text_pdf([(40, 520, "Document No: 2102-20-ED-CV-DWG-1007")])
    response = client.post(
        f"/api/v1/projects/{project.id}/registration/extract",
        headers=headers,
        files=[
            ("files", ("a.pdf", pdf, "application/pdf")),
            ("files", ("b.pdf", other, "application/pdf")),
        ],
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert [row["row_no"] for row in rows] == [1, 2]
    assert gemini["calls"] == 2


def test_low_confidence_marks_the_row_needs_review_and_blocks_save(
    client: TestClient, project, users, gemini
):
    headers = _client_key(client)
    gemini["answer"] = {**ANSWER, "confidence": {"doc_number": 0.42, "title": 0.5}}
    body = _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)).json()
    row = body["rows"][0]
    assert row["needs_review"] is True
    assert any("اطمینان" in message for message in row["validation_messages"])

    blocked = client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={})
    assert blocked.status_code == 422
    assert "نیازمند بررسی" in blocked.json()["detail"]

    # the operator looks at the sheet and confirms
    confirmed = client.post(
        f"/api/v1/projects/{project.id}/registration/rows/{row['id']}/confirm",
        headers=headers,
        json={"confirmed": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["review_confirmed"] is True

    saved = client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={})
    assert saved.status_code == 200, saved.text
    assert saved.json()["count"] == 1


def test_conflicts_also_mark_the_row(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    gemini["answer"] = {**ANSWER, "conflicts": ["شماره چاپ‌شده با کد سرصفحه مغایر است"]}
    row = _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)).json()["rows"][0]
    assert row["needs_review"] is True
    assert row["conflicts"]


def test_a_number_that_breaks_the_regex_is_blocked(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    gemini["answer"] = {**ANSWER, "doc_number": "4501"}
    row = _newest(_upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)))
    assert row["validation_state"] == "blocked"
    assert any("الگوی پروژه" in message for message in row["validation_messages"])
    # and it cannot be confirmed away
    response = client.post(
        f"/api/v1/projects/{project.id}/registration/rows/{row['id']}/confirm",
        headers=headers,
        json={"confirmed": True},
    )
    assert response.status_code == 409


def test_save_writes_the_register_and_the_transmittal(
    client: TestClient, project, users, gemini, db: Session
):
    headers = _client_key(client)
    _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK))
    saved = client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={})
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["count"] == 1
    assert payload["saved"][0]["mdl_created"] is True  # it was not in the MDL

    item = db.query(MDLItem).filter(MDLItem.doc_code == "2102-20-ED-ME-DWG-4501").one()
    assert item.current_rev == "A0"
    revision = db.query(DocumentRevision).filter(DocumentRevision.mdl_item_id == item.id).one()
    assert revision.is_current is True
    assert len(revision.file_hash) == 64  # sha-256 of the stored PDF
    assert revision.status == "registered"
    line = db.query(TransmittalLine).filter(TransmittalLine.revision_id == revision.id).one()
    assert line.line_no == 1

    # the registration is now empty of unsaved rows
    again = client.get(f"/api/v1/projects/{project.id}/registration", headers=headers).json()
    assert [row["saved"] for row in again["rows"]] == [True]
    # and saving twice does nothing rather than duplicating
    second = client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={})
    assert second.status_code == 422


def test_a_higher_revision_supersedes_the_old_one(
    client: TestClient, project, users, gemini, db: Session
):
    headers = _client_key(client)
    _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK))
    assert client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={}).status_code == 200

    gemini["answer"] = {**ANSWER, "revision": "A1"}
    row = _newest(_upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK + [(40, 400, "Rev A1")])))
    assert row["validation_state"] == "supersede"
    assert any("جایگزین" in message for message in row["validation_messages"])

    saved = client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={})
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"][0]["superseded"] is True

    db.expire_all()
    item = db.query(MDLItem).filter(MDLItem.doc_code == "2102-20-ED-ME-DWG-4501").one()
    assert item.current_rev == "A1"
    revisions = {rev.rev: rev for rev in db.query(DocumentRevision).filter(DocumentRevision.mdl_item_id == item.id)}
    assert revisions["A0"].is_current is False
    assert revisions["A0"].status == "superseded"
    assert revisions["A0"].superseded_by_id == revisions["A1"].id
    assert revisions["A1"].is_current is True


def test_an_equal_revision_is_flagged_as_duplicate(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK))
    assert client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={}).status_code == 200

    row = _newest(_upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)))
    assert row["validation_state"] == "duplicate"
    assert any("تکراری" in message for message in row["validation_messages"])
    blocked = client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={})
    assert blocked.status_code == 422


def test_a_lower_revision_is_blocked(client: TestClient, project, users, gemini, db: Session):
    headers = _client_key(client)
    gemini["answer"] = {**ANSWER, "revision": "A2"}
    _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK))
    assert client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={}).status_code == 200

    gemini["answer"] = {**ANSWER, "revision": "A1"}
    row = _newest(_upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)))
    assert row["validation_state"] == "blocked"
    assert any("پایین‌تر" in message for message in row["validation_messages"])
    assert client.post(f"/api/v1/projects/{project.id}/registration/save", headers=headers, json={}).status_code == 422


def test_mdl_status_comes_from_the_register(client: TestClient, project, users, gemini, db: Session):
    headers = _client_key(client)
    item = MDLItem(
        project_id=project.id, doc_code="2102-20-ED-ME-DWG-4501",
        doc_code_norm="2102-20-ED-ME-DWG-4501", title="Tank orientation",
        current_rev="A0", status="Approved", weight=1,
    )
    db.add(item)
    db.commit()

    row = _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)).json()["rows"][0]
    assert row["mdl_status"] == "Approved"


def test_editing_a_row_revalidates_it(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    gemini["answer"] = {**ANSWER, "doc_number": None}
    row = _newest(_upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)))
    assert row["validation_state"] == "blocked"

    fixed = client.patch(
        f"/api/v1/projects/{project.id}/registration/rows/{row['id']}",
        headers=headers,
        json={"doc_no": "2102-20-ED-ME-DWG-4501", "doc_status": "IFC", "doc_percent": 100},
    )
    assert fixed.status_code == 200, fixed.text
    body = fixed.json()
    assert body["validation_state"] == "new"
    assert body["doc_status"] == "IFC"
    assert body["doc_percent"] == 100


def test_remove_row_and_re_extract(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    row = _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)).json()["rows"][0]

    gemini["answer"] = {**ANSWER, "title": "Tank orientation (re-read)"}
    again = client.post(
        f"/api/v1/projects/{project.id}/registration/rows/{row['id']}/re-extract", headers=headers
    )
    assert again.status_code == 200, again.text
    assert again.json()["title"] == "Tank orientation (re-read)"

    removed = client.delete(f"/api/v1/projects/{project.id}/registration/rows/{row['id']}", headers=headers)
    assert removed.status_code == 204
    remaining = client.get(f"/api/v1/projects/{project.id}/registration", headers=headers).json()["rows"]
    assert remaining == []


def test_api_failure_falls_back_to_manual_entry(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    import httpx

    gemini["raise"] = httpx.ConnectError("no route to host")
    body = _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)).json()
    row = body["rows"][0]
    assert row["extraction_status"] == "failed"
    assert row["extraction_attempts"] >= 1
    assert "ارتباط" in (row["extraction_error"] or "")
    assert row["doc_no"] is None  # empty, ready to type
    assert row["validation_state"] == "blocked"  # nothing to validate yet

    # …and all 1 + 2 retries were spent before giving up
    assert gemini["calls"] >= 2


def test_writes_are_document_control_only(client: TestClient, project, users, gemini):
    reader = _client_key(client, "reviewer@edms-test.com")
    response = _upload(client, project.id, reader, build_text_pdf(TITLE_BLOCK))
    assert response.status_code == 403


def test_a_second_page_is_tried_when_the_first_has_no_title_block(
    client: TestClient, project, users, gemini
):
    headers = _client_key(client)
    # page 1 is blank for the extractor, page 2 carries the title block
    blank_then_block = build_text_pdf([(40, 200, "blank page")])
    gemini["answer"] = {
        "doc_number": None, "title": None, "revision": None, "purpose_of_issue": None,
        "confidence": {}, "conflicts": [],
    }
    row = _upload(client, project.id, headers, blank_then_block).json()["rows"][0]
    # nothing readable on either page -> failed, with a reason, still editable
    assert row["extraction_status"] == "failed"
    assert row["extraction_error"]


def test_settings_are_exposed_to_the_screen(client: TestClient, project, users):
    headers = _client_key(client)
    body = client.get(f"/api/v1/projects/{project.id}/registration", headers=headers).json()
    settings = body["settings"]
    assert settings["doc_number_regex"].startswith("^\\d{4}")
    assert settings["revision_regex"] == "^[A-Z]\\d{1,2}$"
    assert settings["confidence_threshold"] == 0.8
    assert settings["discipline_labels"]["ME"] == "ME-Mechanical"


def test_the_project_can_change_the_rules(client: TestClient, project, users, gemini):
    headers = _client_key(client)
    changed = client.put(
        f"/api/v1/projects/{project.id}/settings",
        headers=headers,
        json={
            "settings": {
                "registration": {
                    "discipline_labels": {"ME": "مکانیک — Mechanical"},
                    "confidence_threshold": 0.5,
                }
            }
        },
    )
    assert changed.status_code == 200, changed.text
    row = _upload(client, project.id, headers, build_text_pdf(TITLE_BLOCK)).json()["rows"][0]
    assert row["discipline_label"] == "مکانیک — Mechanical"
