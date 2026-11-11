"""Transmittal cover-sheet PDF rendering (Persian, RTL).

Engine preference: WeasyPrint (best shaping, needs Pango) -> ReportLab +
arabic-reshaper/bidi -> explicit error.  The HTML template is kept inline so the
service has no filesystem dependency inside the worker.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from app.utils.jalali import format_jalali_long

logger = logging.getLogger(__name__)

COVER_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 14mm 12mm 16mm 12mm;
          @bottom-center {{ content: "صفحه " counter(page) " از " counter(pages);
                            font-family: "Vazirmatn", "Noto Naskh Arabic", sans-serif; font-size: 8pt; color: #666; }} }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Vazirmatn", "Noto Naskh Arabic", "DejaVu Sans", sans-serif;
          font-size: 9.5pt; color: #111; margin: 0; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-start;
             border-bottom: 2px solid #1f4e79; padding-bottom: 6px; }}
  .org {{ font-size: 13pt; font-weight: 700; color: #1f4e79; }}
  .org small {{ display: block; font-size: 8.5pt; font-weight: 400; color: #555; }}
  .title-block {{ text-align: center; }}
  .title-block h1 {{ margin: 0; font-size: 15pt; letter-spacing: .5px; }}
  .title-block .sub {{ font-size: 8.5pt; color: #555; }}
  .number {{ border: 1.5px solid #1f4e79; border-radius: 4px; padding: 4px 10px;
             font-size: 11pt; font-weight: 700; color: #1f4e79; direction: ltr; }}
  .meta {{ width: 100%; border-collapse: collapse; margin: 12px 0 10px; }}
  .meta td {{ border: 1px solid #cfd6de; padding: 4px 6px; vertical-align: top; }}
  .meta td.label {{ background: #f2f6fa; width: 22%; color: #333; font-weight: 600; }}
  .meta td.value {{ width: 28%; }}
  table.docs {{ width: 100%; border-collapse: collapse; font-size: 8.5pt; }}
  table.docs th {{ background: #1f4e79; color: #fff; padding: 5px 4px; border: 1px solid #1f4e79; }}
  table.docs td {{ border: 1px solid #cfd6de; padding: 4px; }}
  table.docs tr:nth-child(even) td {{ background: #f7fafd; }}
  .ltr {{ direction: ltr; text-align: left; font-family: "DejaVu Sans Mono", monospace; }}
  .notes {{ margin-top: 10px; border: 1px dashed #9bb0c4; padding: 6px; min-height: 34px; font-size: 8.5pt; }}
  .sign {{ margin-top: 14px; width: 100%; border-collapse: collapse; }}
  .sign td {{ border: 1px solid #cfd6de; height: 58px; vertical-align: top; padding: 4px; font-size: 8pt; }}
  .sign td b {{ color: #1f4e79; }}
  .footer {{ position: fixed; bottom: 6mm; right: 12mm; left: 12mm; font-size: 7.5pt;
             color: #777; border-top: 1px solid #ddd; padding-top: 3px; display: flex;
             justify-content: space-between; }}
  .badge {{ display: inline-block; border: 1px solid #1f4e79; border-radius: 3px;
            padding: 1px 6px; font-size: 8pt; }}
</style>
</head>
<body>
  <div class="header">
    <div class="org">{from_org}<small>{project_name}</small></div>
    <div class="title-block">
      <h1>برگه ارسال مدارک</h1>
      <div class="sub">DOCUMENT TRANSMITTAL</div>
    </div>
    <div class="number">{number}</div>
  </div>

  <table class="meta">
    <tr>
      <td class="label">پروژه</td><td class="value">{project_name}</td>
      <td class="label">شماره برگه ارسال</td><td class="value ltr">{number}</td>
    </tr>
    <tr>
      <td class="label">از (فرستنده)</td><td class="value">{from_org}</td>
      <td class="label">به (گیرنده)</td><td class="value">{to_org}</td>
    </tr>
    <tr>
      <td class="label">هدف ارسال</td><td class="value">{purpose_fa} ({purpose})</td>
      <td class="label">نوع</td><td class="value">{type_fa}</td>
    </tr>
    <tr>
      <td class="label">تاریخ صدور</td><td class="value">{issue_date}</td>
      <td class="label">مهلت پاسخ</td><td class="value">{due_date}</td>
    </tr>
    <tr>
      <td class="label">گیرندگان</td><td class="value" colspan="3">{recipients}</td>
    </tr>
  </table>

  <table class="docs">
    <thead>
      <tr>
        <th style="width:5%">#</th>
        <th style="width:26%">کد مدرک</th>
        <th style="width:34%">عنوان</th>
        <th style="width:9%">بازنگری</th>
        <th style="width:8%">صفحه</th>
        <th style="width:18%">اقدام موردنیاز</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>

  <div class="notes"><b>توضیحات:</b> {remarks}</div>

  <table class="sign">
    <tr>
      <td><b>تهیه‌کننده / کنترل مدارک</b><br><br>{prepared_by}</td>
      <td><b>تأییدکننده</b><br><br>{approved_by}</td>
      <td><b>دریافت‌کننده</b><br>نام و امضا:<br>تاریخ:</td>
    </tr>
  </table>

  <div class="footer">
    <span>این برگه به‌صورت خودکار توسط سامانه EDMS تولید شده است — {generated_at}</span>
    <span class="badge">شناسه: <span class="ltr">{transmittal_id}</span></span>
  </div>
</body>
</html>
"""

PURPOSE_FA = {
    "IFR": "ارسال برای بررسی",
    "IFA": "ارسال برای تأیید",
    "IFC": "ارسال برای اجرا",
    "IFI": "ارسال برای اطلاع",
    "RTC": "پاسخ به نظرات",
    "ASB": "چون‌ساخت (As-Built)",
}
REPLY_SHEET_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<style>
  @page { size: A4 landscape; margin: 12mm 10mm 14mm 10mm;
          @bottom-center { content: "صفحه " counter(page) " از " counter(pages); font-size: 8pt; color: #666; } }
  * { box-sizing: border-box; }
  body { font-family: "Vazirmatn", "Noto Naskh Arabic", "DejaVu Sans", sans-serif; font-size: 8.5pt; color: #111; margin: 0; }
  .header { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #1f4e79; padding-bottom: 5px; }
  .title { text-align: center; font-size: 15pt; font-weight: 700; color: #1f3864; }
  .sub { font-size: 8pt; color: #666; text-align: center; }
  .number { border: 1px solid #1f4e79; padding: 3px 8px; font-weight: 700; direction: ltr; }
  .meta { width: 100%; border-collapse: collapse; margin: 7px 0; }
  .meta td { border: 1px solid #cfd6de; padding: 3px 5px; }
  .meta .label { background: #f2f6fa; font-weight: 700; width: 11%; }
  table.rows { width: 100%; border-collapse: collapse; table-layout: fixed; }
  table.rows th { background: #1f4e79; color: #fff; padding: 4px; border: 1px solid #1f4e79; }
  table.rows td { border: 1px solid #cfd6de; padding: 3px; vertical-align: top; word-wrap: break-word; }
  table.rows tr:nth-child(even) td { background: #f7fafd; }
  .ltr { direction: ltr; text-align: left; font-family: "DejaVu Sans Mono", monospace; }
  .footer { margin-top: 7px; border-top: 1px solid #ddd; padding-top: 3px; color: #777; font-size: 7.5pt; display: flex; justify-content: space-between; }
</style>
</head>
<body>
  <div class="header">
    <div>{from_org}<br><small>{project_name}</small></div>
    <div><div class="title">برگه پاسخ / Reply Sheet</div><div class="sub">COMMENT SHEET RESPONSE</div></div>
    <div class="number">{number}</div>
  </div>
  <table class="meta">
    <tr><td class="label">پروژه</td><td>{project_name}</td><td class="label">ترانسمیتال</td><td class="ltr">{transmittal_number}</td><td class="label">تاریخ ایجاد</td><td>{created_at}</td></tr>
    <tr><td class="label">پاسخ‌دهنده</td><td>{replied_by}</td><td class="label">وضعیت</td><td>{status}</td><td class="label">درصد مدرک</td><td>{doc_percent}%</td></tr>
  </table>
  <table class="rows">
    <thead><tr><th style="width:4%">ردیف</th><th style="width:15%">کد مدرک</th><th style="width:17%">عنوان</th><th style="width:6%">Rev</th><th style="width:5%">کد نظر</th><th style="width:18%">نظر مالک</th><th style="width:18%">پاسخ تأمین‌کننده</th><th style="width:9%">وضعیت</th><th style="width:8%">قضاوت</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="footer"><span>این برگه به‌صورت خودکار توسط سامانه EDMS تولید شده است — {generated_at}</span><span class="ltr">{transmittal_id}</span></div>
</body>
</html>"""


def build_reply_context(
    *,
    number: str,
    project_name: str,
    from_org: str,
    to_org: str,
    transmittal_number: str,
    issue_date: date | datetime | None,
    created_at: date | datetime | None,
    replied_by: str | None,
    status: str,
    doc_percent: int,
    rows: list[dict],
    transmittal_id: str | None = None,
) -> dict:
    rendered_rows = []
    rows_data: list[dict] = []
    for index, row in enumerate(rows, start=1):
        get = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
        rows_data.append({
            key: str(get(key) or "")
            for key in ("doc_code", "title", "rev", "comment_code", "comment_text", "response_text", "status", "judgment")
        })
        rendered_rows.append(
            f"<tr><td>{index}</td>"
            f'<td class="ltr">{_escape(get("doc_code") or "")}</td>'
            f"<td>{_escape(get('title') or '')}</td>"
            f'<td class="ltr">{_escape(get("rev") or "")}</td>'
            f"<td>{_escape(get('comment_code') or '')}</td>"
            f"<td>{_escape(get('comment_text') or '')}</td>"
            f"<td>{_escape(get('response_text') or '')}</td>"
            f"<td>{_escape(get('status') or get('disposition') or '')}</td>"
            f"<td>{_escape(get('judgment') or '')}</td></tr>"
        )
    return {
        "number": _escape(number),
        "project_name": _escape(project_name),
        "from_org": _escape(from_org or "—"),
        "to_org": _escape(to_org or "—"),
        "transmittal_number": _escape(transmittal_number or "—"),
        "issue_date": format_jalali_long(issue_date) if issue_date else "—",
        "created_at": format_jalali_long(created_at) if created_at else "—",
        "replied_by": _escape(replied_by or "—"),
        "status": _escape(status),
        "doc_percent": int(doc_percent or 0),
        "rows": "".join(rendered_rows) or '<tr><td colspan="9">ردیفی ثبت نشده است.</td></tr>',
        "rows_data": rows_data,
        "generated_at": format_jalali_long(datetime.now()),
        "transmittal_id": _escape(transmittal_id or "—"),
    }


def render_reply_sheet(context: dict) -> bytes:
    """Render a Reply Sheet using the same engine fallback as the cover sheet."""
    # Use targeted replacement rather than ``str.format``: the CSS contains
    # ordinary braces and should not be interpreted as template fields.
    html = REPLY_SHEET_HTML
    for key, value in context.items():
        html = html.replace("{" + key + "}", str(value))
    try:
        from weasyprint import HTML  # type: ignore

        return HTML(string=html).write_pdf()
    except Exception as exc:  # pragma: no cover - host-dependent
        logger.warning("WeasyPrint unavailable for Reply Sheet (%s); using ReportLab", exc)
    try:
        return _render_reply_reportlab(context)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("تولید PDF برگه پاسخ ممکن نشد.") from exc


def _render_reply_reportlab(context: dict) -> bytes:  # pragma: no cover - fallback path
    import io

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        def shape(text: str) -> str:
            return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:
        def shape(text: str) -> str:
            return str(text)

    font_name = "Helvetica"
    for candidate in ("/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            pdfmetrics.registerFont(TTFont("EDMSReplyFont", candidate))
            font_name = "EDMSReplyFont"
            break
        except Exception:
            continue
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)
    pdf.setFont(font_name, 14)
    pdf.drawRightString(width - 12 * mm, height - 15 * mm, shape("برگه پاسخ / Reply Sheet"))
    pdf.setFont(font_name, 9)
    y = height - 24 * mm
    for label, key in (("شماره", "number"), ("ترانسمیتال", "transmittal_number"), ("پاسخ‌دهنده", "replied_by"), ("وضعیت", "status")):
        pdf.drawRightString(width - 12 * mm, y, shape(f"{label}: {context[key]}"))
        y -= 5 * mm
    y -= 3 * mm
    columns = [("#", 12), ("Doc", 95), ("Title", 115), ("Rev", 35), ("Code", 32), ("Comment", 190), ("Response", 190), ("Status", 72), ("Judgment", 55)]
    left = 10 * mm
    right = width - 10 * mm
    col_widths = [value / sum(v for _, v in columns) * (right - left) for _, value in columns]
    def draw_header(current_y: float) -> float:
        x = left
        pdf.setFillColorRGB(0.12, 0.31, 0.47)
        pdf.rect(left, current_y - 14, right - left, 14, fill=1, stroke=0)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont(font_name, 7)
        for (label, _), col_width in zip(columns, col_widths):
            pdf.drawCentredString(x + col_width / 2, current_y - 10, shape(label))
            x += col_width
        pdf.setFillColorRGB(0, 0, 0)
        return current_y - 18
    y = draw_header(y)
    pdf.setFont(font_name, 7)
    for index, raw in enumerate(context.get("rows_data", []), start=1):
        values = [str(index), raw.get("doc_code", ""), raw.get("title", ""), raw.get("rev", ""), raw.get("comment_code", ""), raw.get("comment_text", ""), raw.get("response_text", ""), raw.get("status", ""), raw.get("judgment", "")]
        if y < 15 * mm:
            pdf.showPage()
            y = height - 18 * mm
            y = draw_header(y)
        x = left
        for value, col_width in zip(values, col_widths):
            pdf.rect(x, y - 13, col_width, 13, fill=0, stroke=1)
            pdf.drawCentredString(x + col_width / 2, y - 9, shape(str(value)[:65]))
            x += col_width
        y -= 13
    pdf.save()
    return buffer.getvalue()


TYPE_FA = {
    "OT": "ترانسمیتال مهندسی - OT",
    "OS": "ترانسمیتال نهایی - OS",
    "internal": "داخلی",
    "external": "خارجی",
}
ACTION_FA = {
    "review": "بررسی",
    "approve": "تأیید",
    "info": "اطلاع",
    "for_construction": "اجرا",
}


def build_context(
    *,
    number: str,
    project_name: str,
    from_org: str,
    to_org: str,
    purpose: str,
    transmittal_type: str,
    issue_date: date | datetime | None,
    due_date: date | datetime | None,
    recipients: list[str],
    lines: list[dict],
    remarks: str | None,
    prepared_by: str | None,
    approved_by: str | None,
    transmittal_id: str | None = None,
) -> dict:
    rows = "".join(
        f"<tr><td>{index + 1}</td>"
        f'<td class="ltr">{_escape(line.get("doc_code") or "")}</td>'
        f"<td>{_escape(line.get('title') or '')}</td>"
        f'<td class="ltr">{_escape(line.get("rev") or "")}</td>'
        f"<td>{line.get('pages') or ''}</td>"
        f"<td>{ACTION_FA.get(str(line.get('action')), line.get('action') or '')}</td></tr>"
        for index, line in enumerate(lines)
    )
    return {
        "number": _escape(number),
        "project_name": _escape(project_name),
        "from_org": _escape(from_org or "—"),
        "to_org": _escape(to_org or "—"),
        "purpose": _escape(purpose),
        "purpose_fa": PURPOSE_FA.get(purpose, purpose),
        "type_fa": TYPE_FA.get(transmittal_type, transmittal_type),
        "issue_date": format_jalali_long(issue_date) if issue_date else "—",
        "due_date": format_jalali_long(due_date) if due_date else "—",
        "recipients": _escape("، ".join(recipients) or "—"),
        "rows": rows or '<tr><td colspan="6">مدرکی ثبت نشده است.</td></tr>',
        "remarks": _escape(remarks or "—"),
        "prepared_by": _escape(prepared_by or "—"),
        "approved_by": _escape(approved_by or "—"),
        "generated_at": format_jalali_long(datetime.now()),
        "transmittal_id": _escape(transmittal_id or "—"),
    }


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_pdf(context: dict) -> bytes:
    html = COVER_HTML.format(**context)
    try:  # preferred engine — correct Persian shaping via Pango
        from weasyprint import HTML  # type: ignore

        return HTML(string=html).write_pdf()
    except Exception as exc:  # pragma: no cover - depends on host libs
        logger.warning("WeasyPrint unavailable (%s); falling back to ReportLab", exc)

    try:
        return _render_reportlab(context)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "تولید PDF برگه ارسال ممکن نشد: نه WeasyPrint و نه ReportLab در دسترس نیستند."
        ) from exc


def _render_reportlab(context: dict) -> bytes:  # pragma: no cover - fallback path
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        def shape(text: str) -> str:
            return get_display(arabic_reshaper.reshape(str(text)))

    except Exception:  # pragma: no cover

        def shape(text: str) -> str:
            return str(text)

    font_name = "Helvetica"
    for candidate in (
        "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            pdfmetrics.registerFont(TTFont("EDMSFont", candidate))
            font_name = "EDMSFont"
            break
        except Exception:
            continue

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    pdf.setFont(font_name, 14)
    pdf.drawRightString(width - 18 * mm, height - 22 * mm, shape("برگه ارسال مدارک"))
    pdf.setFont(font_name, 10)
    pdf.drawRightString(width - 18 * mm, height - 30 * mm, shape(f"شماره: {context['number']}"))
    pdf.drawRightString(width - 18 * mm, height - 37 * mm, shape(f"پروژه: {context['project_name']}"))
    pdf.drawRightString(width - 18 * mm, height - 44 * mm, shape(f"گیرنده: {context['to_org']}"))
    pdf.drawRightString(width - 18 * mm, height - 51 * mm, shape(f"مهلت پاسخ: {context['due_date']}"))
    y = height - 62 * mm
    pdf.setFont(font_name, 9)
    for index in range(60):
        line_key = f"line_{index}"
        if line_key not in context:
            break
        pdf.drawRightString(width - 18 * mm, y, shape(context[line_key]))
        y -= 6 * mm
    pdf.setFont(font_name, 8)
    pdf.drawRightString(width - 18 * mm, 15 * mm, shape("تولید خودکار توسط سامانه EDMS"))
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
