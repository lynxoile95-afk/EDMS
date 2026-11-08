from __future__ import annotations

from app.models import AccessCircle, Transmittal
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
