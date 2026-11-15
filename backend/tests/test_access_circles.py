from __future__ import annotations

import uuid

from app.models import AccessCircle, ProjectMember, Transmittal
from app.models.enums import PurposeOfIssue, TransmittalStatus, TransmittalType
from tests.conftest import auth_headers


def _create_person(
    client, *, email: str, mobile: str = "09121234567", national_id: str = "1234567891"
) -> dict:
    response = client.post(
        "/api/v1/admin/users",
        headers=auth_headers(client, "admin@edms-test.com"),
        json={
            "email": email,
            "full_name": "مهندس واحد مهندسی",
            "national_id": national_id,
            "mobile": mobile,
            "password": "Secret123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_can_create_person_with_identity_and_put_them_in_circle(client, db, project, users):
    person = _create_person(client, email="engineer@edms-test.com")
    admin = auth_headers(client, "admin@edms-test.com")

    circle_response = client.post(
        f"/api/v1/projects/{project.id}/circles",
        headers=admin,
        json={"code": "ENG", "name": "واحد مهندسی", "description": "پنج نفر پرسنل مهندسی"},
    )
    assert circle_response.status_code == 201, circle_response.text
    circle = circle_response.json()

    member_response = client.post(
        f"/api/v1/projects/{project.id}/members",
        headers=admin,
        json={"user_id": person["id"], "role": "reviewer"},
    )
    assert member_response.status_code == 201, member_response.text
    assert member_response.json()["national_id"] == "1234567891"
    assert member_response.json()["mobile"] == "09121234567"

    circle_member_response = client.post(
        f"/api/v1/projects/{project.id}/circles/{circle['id']}/members",
        headers=admin,
        json={"user_id": person["id"], "can_access_external": True},
    )
    assert circle_member_response.status_code == 201, circle_member_response.text
    body = circle_member_response.json()
    assert body["external_member_count"] == 1
    assert body["members"][0]["national_id"] == "1234567891"
    assert body["members"][0]["can_access_external"] is True


def test_admin_can_assign_and_edit_project_role_and_discipline_from_user_account(client, db, project, users):
    admin = auth_headers(client, "admin@edms-test.com")
    response = client.post(
        "/api/v1/admin/users",
        headers=admin,
        json={
            "email": "role-editor@edms-test.com",
            "full_name": "کاربر دارای نقش",
            "national_id": "1234567891",
            "mobile": "09121234567",
            "password": "Secret123!",
            "project_id": str(project.id),
            "role": "کارشناس DCC",
            "discipline_name": "کنترل کیفیت تجهیزات",
        },
    )
    assert response.status_code == 201, response.text
    person = response.json()
    membership = db.query(ProjectMember).filter_by(project_id=project.id, user_id=uuid.UUID(person["id"])).one()
    assert membership.role == "dcc"
    assert membership.discipline.name == "کنترل کیفیت تجهیزات"

    updated = client.patch(
        f"/api/v1/admin/users/{person['id']}",
        headers=admin,
        json={
            "project_id": str(project.id),
            "role": "هماهنگ‌کننده مدارک پروژه",
            "discipline_id": None,
            "discipline_name": "",
        },
    )
    assert updated.status_code == 200, updated.text
    db.refresh(membership)
    assert membership.role == "هماهنگ‌کننده مدارک پروژه"
    assert membership.discipline_id is None

    listed = client.get(f"/api/v1/projects/{project.id}/members", headers=admin)
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["user_id"] == person["id"])
    assert row["role"] == "هماهنگ‌کننده مدارک پروژه"
    assert row["discipline_id"] is None


def test_circle_add_supports_bulk_selection_and_multiple_project_roles(client, project, users):
    admin = auth_headers(client, "admin@edms-test.com")
    first = _create_person(client, email="bulk-first@edms-test.com")
    second = _create_person(
        client,
        email="bulk-second@edms-test.com",
        mobile="09121234568",
        national_id="9876543210",
    )
    third = _create_person(
        client,
        email="bulk-not-project-member@edms-test.com",
        mobile="09121234569",
        national_id="3141592659",
    )
    for person, roles in ((first, ("reviewer", "project_control")), (second, ("reviewer",))):
        for role in roles:
            response = client.post(
                f"/api/v1/projects/{project.id}/members",
                headers=admin,
                json={"user_id": person["id"], "role": role},
            )
            assert response.status_code == 201, response.text

    single_circle = client.post(
        f"/api/v1/projects/{project.id}/circles",
        headers=admin,
        json={"code": "SINGLE", "name": "عضویت تکی"},
    ).json()
    single_added = client.post(
        f"/api/v1/projects/{project.id}/circles/{single_circle['id']}/members",
        headers=admin,
        json={"user_id": first["id"]},
    )
    assert single_added.status_code == 201, single_added.text

    circle = client.post(
        f"/api/v1/projects/{project.id}/circles",
        headers=admin,
        json={"code": "BULK", "name": "انتخاب گروهی"},
    ).json()
    endpoint = f"/api/v1/projects/{project.id}/circles/{circle['id']}/members/bulk"
    denied = client.post(
        endpoint,
        headers=admin,
        json={"user_ids": [first["id"], third["id"]]},
    )
    assert denied.status_code == 422, denied.text
    listed_circles = client.get(f"/api/v1/projects/{project.id}/circles", headers=admin).json()
    bulk_circle = next(item for item in listed_circles if item["id"] == circle["id"])
    assert bulk_circle["member_count"] == 0

    added = client.post(
        endpoint,
        headers=admin,
        json={"user_ids": [first["id"], second["id"]]},
    )
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["member_count"] == 2
    assert {member["user_id"] for member in body["members"]} == {first["id"], second["id"]}

    duplicate = client.post(
        endpoint,
        headers=admin,
        json={"user_ids": [first["id"], second["id"]]},
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["member_count"] == 2

    added_with_project_membership = client.post(
        endpoint,
        headers=admin,
        json={
            "user_ids": [third["id"]],
            "role": "هماهنگ‌کننده جدید",
            "discipline_name": "کنترل کیفیت جدید",
        },
    )
    assert added_with_project_membership.status_code == 201, added_with_project_membership.text
    assert added_with_project_membership.json()["member_count"] == 3
    project_members = client.get(f"/api/v1/projects/{project.id}/members", headers=admin)
    assert any(
        row["user_id"] == third["id"] and row["role"] == "هماهنگ‌کننده جدید"
        for row in project_members.json()
    )


def test_a_circle_allows_only_one_external_member(client, db, project, users):
    admin = auth_headers(client, "admin@edms-test.com")
    first = _create_person(client, email="engineer-one@edms-test.com", mobile="09121234568")
    second = _create_person(
        client,
        email="engineer-two@edms-test.com",
        mobile="09121234569",
        national_id="9876543210",
    )
    circle = client.post(
        f"/api/v1/projects/{project.id}/circles",
        headers=admin,
        json={"code": "ENG", "name": "واحد مهندسی"},
    ).json()
    for person in (first, second):
        response = client.post(
            f"/api/v1/projects/{project.id}/members",
            headers=admin,
            json={"user_id": person["id"], "role": "reviewer"},
        )
        assert response.status_code == 201, response.text

    added = client.post(
        f"/api/v1/projects/{project.id}/circles/{circle['id']}/members",
        headers=admin,
        json={"user_id": first["id"], "can_access_external": True},
    )
    assert added.status_code == 201, added.text
    denied = client.post(
        f"/api/v1/projects/{project.id}/circles/{circle['id']}/members",
        headers=admin,
        json={"user_id": second["id"], "can_access_external": True},
    )
    assert denied.status_code == 409
    assert "یک عضو" in denied.json()["detail"]


def test_circle_scopes_transmittal_view_and_external_member_can_see_it(client, db, project, users):
    circle = AccessCircle(project_id=project.id, code="ENG", name="واحد مهندسی", created_by_id=users["admin"].id)
    db.add(circle)
    db.flush()
    # The fixture's reviewer is in the project but not in the circle. The DCC is
    # placed in the circle as the external member for this visibility test.
    from app.models import AccessCircleMember

    db.add(AccessCircleMember(circle_id=circle.id, user_id=users["dcc"].id, can_access_external=True))
    transmittal = Transmittal(
        project_id=project.id,
        access_circle_id=circle.id,
        type=TransmittalType.INTERNAL,
        purpose=PurposeOfIssue.IFR,
        status=TransmittalStatus.DRAFT,
        created_by_id=users["dcc"].id,
    )
    db.add(transmittal)
    db.commit()

    outsider_headers = auth_headers(client, "reviewer@edms-test.com")
    outsider = client.get(
        f"/api/v1/transmittals/{transmittal.id}",
        headers=outsider_headers,
    )
    assert outsider.status_code == 404
    outsider_list = client.get(
        f"/api/v1/projects/{project.id}/transmittals", headers=outsider_headers
    )
    assert outsider_list.status_code == 200, outsider_list.text
    assert outsider_list.json()["items"] == []

    external_headers = auth_headers(client, "dcc@edms-test.com")
    external_list = client.get(
        f"/api/v1/projects/{project.id}/transmittals", headers=external_headers
    )
    assert external_list.status_code == 200, external_list.text
    assert [item["id"] for item in external_list.json()["items"]] == [str(transmittal.id)]

    external_member = client.get(
        f"/api/v1/transmittals/{transmittal.id}",
        headers=auth_headers(client, "dcc@edms-test.com"),
    )
    assert external_member.status_code == 200, external_member.text
    assert external_member.json()["access_circle_id"] == str(circle.id)
