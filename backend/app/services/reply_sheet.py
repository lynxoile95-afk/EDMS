"""Reply Sheet — one row per comment, ready for the tab and for the PDF.

The reference system prints the reply sheet per transmittal: every comment of every
document in the sheet, the contractor's answer beside it, and the coordinator's
disposition. This module is the single source of those rows; the tab renders them and
``rendering.build_reply_context`` prints them, so the screen and the paper can never
drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transmittal, TransmittalLine


@dataclass(frozen=True)
class ReplySheetRow:
    """One comment line of the reply sheet."""

    doc_code: str | None
    title: str | None
    rev: str | None
    line_no: int
    comment_code: str
    comment_text: str
    author_name: str | None
    response_text: str | None
    disposition: str | None
    status: str
    created_at: datetime

    def as_dict(self) -> dict:
        return {
            "doc_code": self.doc_code,
            "title": self.title,
            "rev": self.rev,
            "line_no": self.line_no,
            "comment_code": self.comment_code,
            "comment_text": self.comment_text,
            "author_name": self.author_name,
            "response_text": self.response_text,
            "disposition": self.disposition,
            "status": self.status,
            "created_at": self.created_at,
        }


def reply_sheet_rows(db: Session, transmittal: Transmittal) -> list[ReplySheetRow]:
    """Every comment of every document in the transmittal, in sheet order.

    Documents are ordered by the transmittal's own line numbering, comments by the
    moment they were registered. A document without comments contributes nothing; a
    transmittal without comments returns an empty list rather than a placeholder row.
    """
    lines = (
        db.execute(
            select(TransmittalLine)
            .where(TransmittalLine.transmittal_id == transmittal.id)
            .order_by(TransmittalLine.line_no)
        )
        .scalars()
        .all()
    )

    rows: list[ReplySheetRow] = []
    for line in lines:
        revision = line.revision
        if revision is None:
            continue
        item = revision.mdl_item
        for comment in sorted(revision.comments, key=lambda c: (c.created_at, c.seq_no)):
            rows.append(
                ReplySheetRow(
                    doc_code=item.doc_code if item else line.doc_code,
                    title=item.title if item else None,
                    rev=revision.rev,
                    line_no=line.line_no,
                    comment_code=str(comment.code),
                    comment_text=comment.text,
                    author_name=comment.author.full_name if comment.author else None,
                    response_text=comment.response_text,
                    disposition=str(comment.disposition) if comment.disposition else None,
                    status=str(comment.status),
                    created_at=comment.created_at,
                )
            )
    return rows
