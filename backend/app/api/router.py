"""Aggregate API router (v1)."""

from fastapi import APIRouter

from app.api.routes import (
    admin,
    auth,
    comments,
    dashboard,
    ddm,
    documents,
    files,
    mdl,
    notifications,
    projects,
    review,
    system,
    transmittals,
    uploads,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(mdl.router)
api_router.include_router(uploads.router)
api_router.include_router(review.router)
api_router.include_router(documents.router)
api_router.include_router(transmittals.router)
api_router.include_router(ddm.router)
api_router.include_router(comments.router)
api_router.include_router(files.router)
api_router.include_router(notifications.router)
api_router.include_router(dashboard.router)
api_router.include_router(admin.router)
api_router.include_router(system.router)
