"""The customer's document-coding structure (sample MDL ``2102-20-ED-GN-MDL-9001-A8``).

The sample numbers every document with six segments:

    2102 - 20 - EB - GN - MDL - 9001
    project unit phase discipline type serial

These tests pin that structure down: the parser, the pattern the decider validates
with, the group-name mapping the sheet uses ("CIVIL & STRUCTURE" → ``CV``), and the
path through the Excel import, which must fill the segment columns from the code even
when the sheet's own columns are empty.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.services import code_structure

from app.services.code_structure import (
    CODE_PATTERN_EXAMPLE,
    build_code,
    group_to_discipline,
    parse_code,
)
from app.services.project_settings import merge_settings
from tests.conftest import auth_headers

#: Headers exactly as printed on page 3 of the attached sample.
SAMPLE_HEADERS = [
    "Row",
    "Discipline",
    "Category",
    "Serial No.",
    "Document No.",
    "Document Title",
    "BASIC / DETAIL",
    "Class",
    "P.O.I",
    "REMARK",
]

SAMPLE_ROWS = [
    ["1-1", "GENERAL", "MDL", "9001", "2102-20-EB-GN-MDL-9001", "MASTER DOCUMENT LIST", "B", "R", "IFR", ""],
    ["1-12", "GENERAL", "INX", "9012", "2102-20-ED-GN-INX-9012", "INDEX OF DRAWINGS", "D", "R", "IFR", ""],
    ["2-1", "CIVIL & STRUCTURE", "STU", "1001", "2102-20-EB-CV-STU-1001", "TOPGRAPHY SURVEY", "B", "A", "IFR", "MODIFY"],
    ["2-3", "CIVIL & STRUCTURE", "DWG", "1003", "2102-20-ED-CV-DWG-1003", "FOUNDATION LOCATION", "D", "A", "IFR", "MODIFY"],
]


def _xlsx(rows: list[list], headers: list[str]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "MDL"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- parser
def test_the_sample_code_splits_into_six_segments():
    assert parse_code(CODE_PATTERN_EXAMPLE) == {
        "project": "2102",
        "unit": "20",
        "phase": "EB",
        "discipline": "GN",
        "type": "MDL",
        "serial": "9001",
    }


def test_phase_is_read_from_the_code():
    parsed = parse_code("2102-20-ED-CV-DWG-1003")
    assert parsed["phase"] == "ED"
    assert parsed["discipline"] == "CV"
    assert parsed["type"] == "DWG"
    assert parsed["serial"] == "1003"


def test_legacy_four_part_codes_still_parse():
    # The demo project and the older tests use PRJ-CIV-DR-0101.
    assert parse_code("PRJ-CIV-DR-0101") == {
        "project": "PRJ",
        "discipline": "CIV",
        "type": "DR",
        "serial": "0101",
    }


def test_build_and_parse_round_trip():
    code = build_code(
        project="2102", unit="20", phase="EB", discipline="GN", doc_type="MDL", serial="9001"
    )
    assert code == "2102-20-EB-GN-MDL-9001"
    assert parse_code(code)["serial"] == "9001"


def test_junk_does_not_parse():
    for bad in ("", "hello", "2102-20-EB-GN-MDL", "2102-2-EB-GN-MDL-9001", "2102-20-EB-GN-MDL-90O1"):
        assert parse_code(bad) == {}, bad


def test_the_default_pattern_accepts_the_sample_and_the_legacy_shape():
    import re

    pattern = merge_settings(None)["doc_code_pattern"]
    for good in ("2102-20-EB-GN-MDL-9001", "PRJ-CIV-DR-0101", "TST-MEC-DR-001"):
        assert re.fullmatch(pattern, good), good
    for bad in ("2102-20-EB-GN-MDL", "not-a-code"):
        assert not re.fullmatch(pattern, bad), bad


def test_sheet_group_names_map_to_discipline_codes():
    assert group_to_discipline("GENERAL") == "GN"
    assert group_to_discipline("CIVIL & STRUCTURE") == "CV"
    assert group_to_discipline("ELECTRICAL") == "EL"
    assert group_to_discipline("") is None


# --------------------------------------------------------------------------- import
def test_import_reads_the_sample_layout(client: TestClient, project, users):
    """The sheet's own columns are sparse; the code fills the rest."""
    dcc = auth_headers(client, "dcc@edms-test.com")
    payload = _xlsx(SAMPLE_ROWS, SAMPLE_HEADERS)

    preview = client.post(
        f"/api/v1/projects/{project.id}/mdl/preview",
        headers=dcc,
        files={"file": ("MDL.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert preview.status_code == 200, preview.text
    mapping = preview.json()["suggested_mapping"]
    # the sheet's "Category" column is the document type, "Serial No." the serial
    assert mapping["doc_code"] == 4
    assert mapping["title"] == 5
    assert mapping["doc_type"] == 2
    assert mapping["serial_no"] == 3
    assert mapping["basic_detail"] == 6
    assert mapping["doc_class"] == 7
    assert mapping["poi"] == 8
    assert mapping["remark"] == 9

    imported = client.post(
        f"/api/v1/projects/{project.id}/mdl/import",
        headers=dcc,
        files={"file": ("MDL.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"config": __import__("json").dumps({"mapping": mapping})},
    )
    assert imported.status_code == 200, imported.text
    report = imported.json()
    assert report["errors"] == 0, report
    assert report["imported"] == len(SAMPLE_ROWS)

    rows = {
        item["doc_code"]: item
        for item in client.get(
            f"/api/v1/projects/{project.id}/mdl/items?size=50", headers=dcc
        ).json()["items"]
    }
    master = rows["2102-20-EB-GN-MDL-9001"]
    assert master["phase"] == "EB"
    assert master["serial_no"] == "9001"
    assert master["doc_type"] == "MDL"
    assert master["discipline_code"] == "GN"
    assert master["unit_code"] == "20"
    assert master["category"] == "GENERAL"        # the readable group name is kept
    assert master["doc_class"] == "R"
    assert master["basic_detail"] == "B"
    assert master["poi"] == "IFR"

    civil = rows["2102-20-ED-CV-DWG-1003"]
    assert civil["phase"] == "ED"
    # the customer writes CV; this project's lookup is CIV — the alias resolves it
    # rather than creating a second, duplicate discipline
    assert civil["discipline_code"] in {"CV", "CIV"}
    assert civil["remark"] == "MODIFY"


def test_manual_row_takes_its_segments_from_the_code(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    created = client.post(
        f"/api/v1/projects/{project.id}/mdl/items",
        headers=dcc,
        json={"doc_code": "2102-20-ED-EL-SLD-2001", "title": "Single Line Diagram"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["phase"] == "ED"
    assert body["serial_no"] == "2001"
    assert body["doc_type"] == "SLD"
    assert body["discipline_code"] in {"EL", "ELE"}
    assert body["unit_code"] == "20"


def test_a_code_outside_the_structure_is_refused_with_the_pattern(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/projects/{project.id}/mdl/items",
        headers=dcc,
        json={"doc_code": "2102-20-EB-GN-MDL", "title": "بدون سریال"},
    )
    assert response.status_code == 422, response.text


def test_the_structure_is_readable_from_the_api(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    data = client.get(f"/api/v1/projects/{project.id}/mdl/code-structure", headers=dcc).json()
    assert data["pattern_label"] == "PROJECT-UNIT-PHASE-DISC-TYPE-SERIAL"
    assert data["example"] == "2102-20-EB-GN-MDL-9001"
    assert [segment["key"] for segment in data["segments"]] == [
        "project",
        "unit",
        "phase",
        "discipline",
        "type",
        "serial",
    ]
    assert any(item["code"] == "CV" for item in data["disciplines"])


# --------------------------------------------------------------------------- serial blocks
def test_serial_block_warning_for_an_outlier():
    from app.services.code_structure import serial_outside_block

    # the sample's civil block is 1xxx and piping 5xxx
    assert serial_outside_block("CV", "1003") is None
    assert serial_outside_block("PI", "5001") is None
    assert serial_outside_block("ME", "4569") is None
    # one mechanical row in the sample carries serial 501 — an outlier worth flagging
    assert serial_outside_block("ME", "501") == (4000, 4999)
    assert serial_outside_block("ZZ", "501") is None  # unknown discipline: no opinion


def test_import_warns_but_does_not_reject_an_outlier_serial(client: TestClient, project, users):
    import json

    dcc = auth_headers(client, "dcc@edms-test.com")
    rows = [["5-1", "MECHANICAL", "DWG", "501", "2102-20-ED-ME-DWG-501", "OUTLIER", "D", "A", "IFR", ""]]
    payload = _xlsx(rows, SAMPLE_HEADERS)
    mapping = client.post(
        f"/api/v1/projects/{project.id}/mdl/preview",
        headers=dcc,
        files={"file": ("MDL.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()["suggested_mapping"]

    imported = client.post(
        f"/api/v1/projects/{project.id}/mdl/import",
        headers=dcc,
        files={"file": ("MDL.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"config": json.dumps({"mapping": mapping})},
    )
    assert imported.status_code == 200, imported.text
    report = imported.json()
    assert report["errors"] == 0
    assert report["imported"] == 1
    assert any("بازه دیسیپلین" in entry["message"] for entry in report["entries"])


def test_structure_description_lists_one_serial_block_per_discipline() -> None:
    """The legend in the UI is generated from the service, so the blocks must ship too."""
    data = code_structure.structure_description()
    blocks = {row["discipline"]: (row["from"], row["to"]) for row in data["serial_blocks"]}
    assert blocks["PR"] == (100, 199)
    assert blocks["CV"] == (1000, 1999)
    assert blocks["IN"] == (7000, 7999)
    # aliases must not double the table: one row per canonical discipline
    assert "CIV" not in blocks and "MEC" not in blocks
    assert len(data["segments"]) == 6


# --------------------------------------------------------------- the manual form
def test_parse_code_endpoint_reads_the_form_fields_from_the_code(
    client: TestClient, project, users
):
    """The manual dialog asks for the code; everything else comes back from here."""
    dcc = auth_headers(client, "dcc@edms-test.com")
    response = client.get(
        f"/api/v1/projects/{project.id}/mdl/parse-code",
        params={"code": "2102-20-ED-ME-DWG-4501"},
        headers=dcc,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["fields"]["unit_code"] == "20"
    assert data["fields"]["doc_type"] == "DWG"
    assert data["fields"]["phase"] == "ED"
    assert data["fields"]["serial_no"] == "4501"
    assert data["labels"]["doc_type"] == "نقشه"
    assert data["labels"]["phase"].startswith("Detail")
    assert data["duplicate"] is False
    assert data["warnings"] == []


def test_parse_code_reports_a_bad_code_instead_of_guessing(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    data = client.get(
        f"/api/v1/projects/{project.id}/mdl/parse-code",
        params={"code": "2102-20-ED-ME-DWG"},
        headers=dcc,
    ).json()
    assert data["ok"] is False
    assert data["fields"] == {}
    assert "سازگار نیست" in data["error"]


def test_parse_code_warns_about_the_serial_block_and_a_duplicate(
    client: TestClient, project, users
):
    dcc = auth_headers(client, "dcc@edms-test.com")
    created = client.post(
        f"/api/v1/projects/{project.id}/mdl/items",
        headers=dcc,
        json={"doc_code": "2102-20-ED-ME-DWG-4101", "title": "Tank Layout"},
    )
    assert created.status_code == 201, created.text

    again = client.get(
        f"/api/v1/projects/{project.id}/mdl/parse-code",
        params={"code": "2102-20-ED-ME-DWG-4101"},
        headers=dcc,
    ).json()
    assert again["ok"] is True
    assert again["duplicate"] is True

    # 1005 sits in the civil block, not the mechanical one: warn, never block
    outlier = client.get(
        f"/api/v1/projects/{project.id}/mdl/parse-code",
        params={"code": "2102-20-ED-ME-DWG-1005"},
        headers=dcc,
    ).json()
    assert outlier["ok"] is True
    assert outlier["duplicate"] is False
    assert any("بیرون از بازه" in warning for warning in outlier["warnings"])


def test_a_manual_row_needs_only_code_title_poi_and_weight(client: TestClient, project, users):
    """The four fields the operator types are enough; the rest is derived."""
    dcc = auth_headers(client, "dcc@edms-test.com")
    created = client.post(
        f"/api/v1/projects/{project.id}/mdl/items",
        headers=dcc,
        json={
            "doc_code": "2102-20-ED-CV-DWG-1007",
            "title": "Foundation Layout",
            "poi": "IFR",
            "weight": 2,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["poi"] == "IFR"
    assert body["weight"] == 2
    assert body["unit_code"] == "20"
    assert body["doc_type"] == "DWG"
    assert body["discipline_code"] in {"CV", "CIV"}
    assert body["status"] == "Draft"  # defaulted, not asked for


def test_discipline_spellings_collapse_to_one_code():
    """The title block says MECHANICAL, the MDL says MEC — not a mismatch."""
    for spelling in ("MECHANICAL", "Mechanical / مکانیک", "MEC", "mec", "ME"):
        assert code_structure.canonical_discipline(spelling) == "ME"
    assert code_structure.canonical_discipline("CIVIL & STRUCTURE") == "CV"
    assert code_structure.canonical_discipline("ELECTRICAL") == "EL"
    assert code_structure.canonical_discipline("") == ""
