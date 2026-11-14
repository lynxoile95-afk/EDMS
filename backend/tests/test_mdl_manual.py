"""MDL is document control's register: they load it, everybody else reads it.

Two doors in — the Excel import and a single hand-typed row — and the same rules
behind both: unique document code, existing discipline/unit, and a write ACL that
refuses every role except ``dcc``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers


def _new_row(**overrides) -> dict:
    payload = {
        "doc_code": "TST-CIV-DR-900",
        "title": "Foundation Detail / جزئیات فونداسیون",
        "discipline_code": "CIV",
        "unit_code": "U-100",
        "doc_type": "DWG",
        "current_rev": "A",
        "status": "Draft",
        "weight": 1.0,
    }
    payload.update(overrides)
    return payload


def test_document_control_can_add_a_row_by_hand(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    created = client.post(f"/api/v1/projects/{project.id}/mdl/items", headers=dcc, json=_new_row())

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["doc_code"] == "TST-CIV-DR-900"
    assert body["discipline_code"] == "CIV"
    assert body["current_rev"] == "A"

    listed = client.get(f"/api/v1/projects/{project.id}/mdl/items?q=TST-CIV-DR-900", headers=dcc)
    assert listed.status_code == 200
    assert any(item["doc_code"] == "TST-CIV-DR-900" for item in listed.json()["items"])


def test_a_duplicate_document_code_is_refused(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    first = client.post(f"/api/v1/projects/{project.id}/mdl/items", headers=dcc, json=_new_row())
    assert first.status_code == 201, first.text

    second = client.post(
        f"/api/v1/projects/{project.id}/mdl/items", headers=dcc, json=_new_row(title="عنوان دیگر")
    )
    assert second.status_code == 409, second.text
    assert "قبلاً" in second.json()["detail"]


def test_unknown_discipline_or_unit_is_refused(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    bad_discipline = client.post(
        f"/api/v1/projects/{project.id}/mdl/items", headers=dcc, json=_new_row(discipline_code="XXX")
    )
    assert bad_discipline.status_code == 422, bad_discipline.text
    assert "XXX" in bad_discipline.json()["detail"]

    bad_unit = client.post(
        f"/api/v1/projects/{project.id}/mdl/items",
        headers=dcc,
        json=_new_row(doc_code="TST-CIV-DR-901", unit_code="U-999"),
    )
    assert bad_unit.status_code == 422, bad_unit.text


def test_only_document_control_may_change_the_mdl(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    created = client.post(f"/api/v1/projects/{project.id}/mdl/items", headers=dcc, json=_new_row())
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    for email in ("reviewer@edms-test.com", "submitter@edms-test.com", "pm@edms-test.com", "em@edms-test.com"):
        headers = auth_headers(client, email)
        manual = client.post(
            f"/api/v1/projects/{project.id}/mdl/items",
            headers=headers,
            json=_new_row(doc_code=f"TST-CIV-DR-{abs(hash(email)) % 800 + 100}"),
        )
        assert manual.status_code == 403, f"{email} could add a row: {manual.text}"

        patched = client.patch(
            f"/api/v1/projects/{project.id}/mdl/items/{item_id}",
            headers=headers,
            json={"title": "تغییر غیرمجاز"},
        )
        assert patched.status_code == 403, f"{email} could edit a row: {patched.text}"

        deleted = client.delete(
            f"/api/v1/projects/{project.id}/mdl/items/{item_id}", headers=headers
        )
        assert deleted.status_code == 403, f"{email} could delete a row: {deleted.text}"

    # and the row is still exactly as document control wrote it
    listed = client.get(f"/api/v1/projects/{project.id}/mdl/items?q=TST-CIV-DR-900", headers=dcc)
    item = next(row for row in listed.json()["items"] if row["doc_code"] == "TST-CIV-DR-900")
    assert item["title"] == "Foundation Detail / جزئیات فونداسیون"


def test_excel_import_is_document_control_only(client: TestClient, project, users):
    """The import endpoint refuses a manager even before it looks at the file."""
    pm = auth_headers(client, "pm@edms-test.com")
    response = client.post(
        f"/api/v1/projects/{project.id}/mdl/import",
        headers=pm,
        files={"file": ("mdl.xlsx", b"not really a workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"config": '{"mapping": {}}'},
    )
    assert response.status_code == 403, response.text


def test_a_row_with_revisions_cannot_be_deleted(client: TestClient, project, users, db, ddm_rules):
    from app.models import MDLItem
    from tests.test_api_flow import _seed_result

    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")
    registered = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=dcc,
        json={"action": "register", "mdl_item_id": str(item.id), "rev": "B"},
    )
    assert registered.status_code == 200, registered.text

    response = client.delete(f"/api/v1/projects/{project.id}/mdl/items/{item.id}", headers=dcc)
    assert response.status_code == 409, response.text
    assert "بازنگری" in response.json()["detail"]
