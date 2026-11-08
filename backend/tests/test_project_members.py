from __future__ import annotations

from tests.conftest import auth_headers


def test_member_accepts_new_typed_discipline_and_can_clear_it(client, project, users):
    admin = auth_headers(client, "admin@edms-test.com")
    member = next(
        item for item in client.get(f"/api/v1/projects/{project.id}/members", headers=admin).json()
        if item["email"] == "reviewer@edms-test.com"
    )
    custom_name = "کنترل کیفیت تجهیزات"

    updated = client.patch(
        f"/api/v1/projects/{project.id}/members/{member['id']}",
        headers=admin,
        json={"role": "reviewer", "discipline_id": None, "discipline_name": custom_name},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["discipline_name"] == custom_name
    assert body["discipline_code"].startswith("CUSTOM-")

    listed = client.get(f"/api/v1/projects/{project.id}/members", headers=admin)
    assert listed.status_code == 200, listed.text
    persisted = next(item for item in listed.json() if item["id"] == member["id"])
    assert persisted["discipline_name"] == custom_name

    disciplines = client.get(f"/api/v1/projects/{project.id}/disciplines", headers=admin)
    assert disciplines.status_code == 200, disciplines.text
    assert any(item["name"] == custom_name for item in disciplines.json())

    cleared = client.patch(
        f"/api/v1/projects/{project.id}/members/{member['id']}",
        headers=admin,
        json={"role": "reviewer", "discipline_id": None, "discipline_name": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["discipline_id"] is None
    assert cleared.json()["discipline_name"] is None


def test_add_member_accepts_new_typed_discipline(client, project, users):
    admin = auth_headers(client, "admin@edms-test.com")

    response = client.post(
        f"/api/v1/projects/{project.id}/members",
        headers=admin,
        json={
            "user_id": str(users["em"].id),
            "role": "reviewer",
            "discipline_id": None,
            "discipline_name": "ایمنی فرایند",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["discipline_name"] == "ایمنی فرایند"
    assert body["discipline_code"].startswith("CUSTOM-")
