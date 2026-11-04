"""Manual transmittal registration from project-scoped MDL rows."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers


def test_manual_transmittal_uses_selected_mdl_rows(client: TestClient, project, users, db, ddm_rules):
    from app.models import MDLItem, DocumentRevision, Transmittal

    first = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-CIV-DR-001").one()
    second = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-MEC-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/projects/{project.id}/transmittals/manual",
        headers=dcc,
        json={
            "rows": [
                {"mdl_item_id": str(first.id), "revision": "A", "page_count": 3},
                {"mdl_item_id": str(second.id), "revision": "B", "page_count": 5, "note": "بازنگری دستی"},
            ],
            "purpose": "IFR",
            "type": "internal",
            "from_org": "پیمانکار",
            "to_org": "کارفرما",
            "remarks": "ترانسمیتال ثبت‌شده به‌صورت دستی",
            "extra_recipient_ids": [str(users["reviewer"].id)],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["project_id"] == str(project.id)
    assert body["remarks"] == "ترانسمیتال ثبت‌شده به‌صورت دستی"
    assert body["from_org"] == "پیمانکار"
    assert body["to_org"] == "کارفرما"
    assert len(body["lines"]) == 2
    assert any(line["rev"] == "B" for line in body["lines"])
    assert len(body["recipients"]) >= 1
    assert db.query(DocumentRevision).filter_by(project_id=project.id).count() == 2
    assert db.query(Transmittal).filter_by(project_id=project.id).count() == 1


def test_manual_transmittal_rejects_a_row_from_another_project(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/projects/{project.id}/transmittals/manual",
        headers=dcc,
        json={
            "rows": [{"mdl_item_id": "00000000-0000-0000-0000-000000000000", "revision": "A"}],
            "extra_recipient_ids": [str(users["reviewer"].id)],
        },
    )
    assert response.status_code == 422
