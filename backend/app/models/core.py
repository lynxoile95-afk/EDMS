"""Projects, users, memberships, disciplines, units and title-block templates."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProjectRole


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(255))
    contractor_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    #: project-scoped configuration (doc code pattern, thresholds, numbering, ...)
    settings: Mapped[dict] = mapped_column(default=dict)

    disciplines: Mapped[list["Discipline"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    units: Mapped[list["Unit"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    auth_provider: Mapped[str] = mapped_column(String(32), default="local", server_default="local")
    external_id: Mapped[str | None] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(8), default="fa", server_default="fa")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["ProjectMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class ProjectMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Role of a user inside a project (optionally narrowed to a discipline)."""

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", "role", "discipline_id", name="uq_member_scope"),
        Index("ix_member_project_user", "project_id", "user_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[ProjectRole] = mapped_column(String(32), nullable=False)
    discipline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("disciplines.id", ondelete="SET NULL"))

    user: Mapped[User] = relationship(back_populates="memberships", lazy="joined")
    project: Mapped[Project] = relationship(lazy="joined")
    discipline: Mapped["Discipline | None"] = relationship(lazy="joined")


class Discipline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "disciplines"
    __table_args__ = (UniqueConstraint("project_id", "code", name="uq_discipline_code"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    project: Mapped[Project] = relationship(back_populates="disciplines")


class Unit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Process / plant unit (e.g. U-100 Utilities)."""

    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("project_id", "code", name="uq_unit_code"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    project: Mapped[Project] = relationship(back_populates="units")


class TitleBlockTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-project title-block geometry.

    ``regions`` maps a field name to a normalised box relative to the page:
    ``{"doc_code": {"x": 0.62, "y": 0.71, "w": 0.35, "h": 0.04}, ...}``.
    Because engineering sheets are usually drawn on a constant aspect ratio,
    normalised coordinates work for A0..A4 alike.
    """

    __tablename__ = "title_block_templates"
    __table_args__ = (Index("ix_tbt_project_active", "project_id", "is_active"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: page width/height ratio window this template applies to (e.g. [[1.3,1.5]])
    page_aspect_ratios: Mapped[list] = mapped_column(default=list)
    #: normalised ROI per field
    regions: Mapped[dict] = mapped_column(default=dict)
    #: keywords that must appear in the page for this template to be a candidate
    anchor_keywords: Mapped[list] = mapped_column(default=list)
    #: field-name -> regex override
    field_regex: Mapped[dict] = mapped_column(default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
