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


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(255))
    contractor_name: Mapped[str | None] = mapped_column(String(255))
    # Two-letter company identifiers are kept on the project itself because they
    # are part of the immutable identity of every newly issued transmittal.
    # Existing projects may remain NULL until an administrator completes them.
    contractor_code: Mapped[str | None] = mapped_column(String(2))
    parent_company_code: Mapped[str | None] = mapped_column(String(2))
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
    #: Iranian national identifier and mobile are kept as strings to preserve leading zeroes.
    #: They are nullable only for legacy rows created before identity data became mandatory;
    #: all new accounts are validated as required by ``UserCreate``.
    national_id: Mapped[str | None] = mapped_column(String(10), unique=True, index=True)
    mobile: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
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
    circle_memberships: Mapped[list["AccessCircleMember"]] = relationship(
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
    # Built-in workflow roles use ProjectRole values, while the administration UI
    # also permits a project-specific free-text title (for example, "کارشناس DCC").
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    discipline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("disciplines.id", ondelete="SET NULL"))

    user: Mapped[User] = relationship(back_populates="memberships", lazy="joined")
    project: Mapped[Project] = relationship(lazy="joined")
    discipline: Mapped["Discipline | None"] = relationship(lazy="joined")


class AccessCircle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A project-scoped group that defines a private collaboration boundary.

    A user may belong to several circles, but external visibility is deliberately
    granted on the membership row. This makes the common rule explicit: everyone
    in a circle can collaborate with the circle, while at most one member may be
    allowed to see and route work outside it.
    """

    __tablename__ = "access_circles"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_access_circle_project_code"),
        Index("ix_access_circle_project_active", "project_id", "is_active"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    project: Mapped["Project"] = relationship(lazy="joined")
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_id], lazy="joined")
    members: Mapped[list["AccessCircleMember"]] = relationship(
        back_populates="circle", cascade="all, delete-orphan", lazy="selectin"
    )


class AccessCircleMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Membership of a person in a project access circle."""

    __tablename__ = "access_circle_members"
    __table_args__ = (
        UniqueConstraint("circle_id", "user_id", name="uq_access_circle_member"),
        Index("ix_access_circle_member_user", "user_id", "circle_id"),
    )

    circle_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("access_circles.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    #: Only this explicit flag opens the member's view/routing outside the circle.
    can_access_external: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    circle: Mapped[AccessCircle] = relationship(back_populates="members", lazy="joined")
    user: Mapped[User] = relationship(back_populates="circle_memberships", lazy="joined")


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
