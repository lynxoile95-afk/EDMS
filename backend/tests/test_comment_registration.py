"""Full Comment Registration form, attachment policy and numbering coverage."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import MDLItem
from tests.test_api_flow import _seed_result


def _auth(client: TestClient, email: str) -> dict[str, str]:
    from tests.conftest import auth_headers

    return auth_headers(client, email)


def _revision_id(client: TestClient, db, project, users) -> str:
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    response = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=_auth(client, "dcc@edms-test.com"),
        json={"action": "register", "mdl_item_id": str(item.id), "rev": "B"},
    )
    assert response.status_code == 200, response.text
    return response.json()["revision_id"]


def test_multipart_registration_stores_metadata_and_attachment(client: TestClient, db, project, users):
    revision_id = _revision_id(client, db, project, users)
    headers = _auth(client, "reviewer@edms-test.com")
    response = client.post(
        f"/api/v1/revisions/{revision_id}/comments/multipart",
        headers=headers,
        data={
            "text": "صفحه‌بندی نقشه با دیتاشیت منطبق نیست",
            "code": "2",
            "discipline_code": "CIV",
            "from_page_no": "2",
            "to_page_no": "4",
            "disposition": "commented",
            "status_change_date": "2026-10-24",
        },
        files={"files": ("evidence.pdf", b"%PDF-test", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["seq_no"] == 1
    assert body["registration_id"] == body["id"]
    assert body["from_page_no"] == 2
    assert body["to_page_no"] == 4
    assert body["discipline_code"] == "CIV"
    assert body["attachment_count"] == 1
    attachment = body["attachments"][0]

    downloaded = client.get(attachment["download_url"], headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-test"
    assert "attachment" in downloaded.headers["content-disposition"]


def test_edit_can_add_and_remove_attachments_without_renumbering(client: TestClient, db, project, users):
    revision_id = _revision_id(client, db, project, users)
    headers = _auth(client, "reviewer@edms-test.com")
    first = client.post(
        f"/api/v1/revisions/{revision_id}/comments/multipart",
        headers=headers,
        data={"text": "نظر اولیه", "code": "3"},
        files={"files": ("first.png", b"png", "image/png")},
    )
    assert first.status_code == 201, first.text
    comment = first.json()

    edited = client.patch(
        f"/api/v1/comments/{comment['id']}",
        headers=headers,
        data={"text": "نظر ویرایش‌شده", "code": "3"},
        files={"files": ("second.zip", b"zip", "application/zip")},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["seq_no"] == 1
    assert edited.json()["attachment_count"] == 2

    old_attachment = next(item for item in edited.json()["attachments"] if item["file_name"] == "first.png")
    removed = client.delete(
        f"/api/v1/comments/{comment['id']}/attachments/{old_attachment['id']}",
        headers=headers,
    )
    assert removed.status_code == 204, removed.text

    listed = client.get(f"/api/v1/revisions/{revision_id}/comment-lists", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["comments"][0]["attachment_count"] == 1


def test_server_rejects_invalid_page_range_and_attachment_extension(client: TestClient, db, project, users):
    revision_id = _revision_id(client, db, project, users)
    headers = _auth(client, "reviewer@edms-test.com")
    bad_range = client.post(
        f"/api/v1/revisions/{revision_id}/comments/multipart",
        headers=headers,
        data={"text": "نظر معتبر", "from_page_no": "9", "to_page_no": "3"},
    )
    assert bad_range.status_code == 422

    bad_extension = client.post(
        f"/api/v1/revisions/{revision_id}/comments/multipart",
        headers=headers,
        data={"text": "نظر معتبر"},
        files={"files": ("malware.exe", b"not allowed", "application/octet-stream")},
    )
    assert bad_extension.status_code == 415
