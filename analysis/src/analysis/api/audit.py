"""Recording security events, and the admin route that lists them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..db import UserRow
from .deps import ApiStore, Store, require_admin
from .schemas import AuditOut, AuditPage, ErrorOut

ERR = {"model": ErrorOut}
audit_router = APIRouter(
    prefix="/api/v1/audit",
    tags=["audit"],
    dependencies=[Depends(require_admin)],
    responses={401: ERR, 403: ERR},
)


async def audit(
    request: Request,
    store: ApiStore,
    action: str,
    *,
    actor: UserRow | None = None,
    actor_username: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record one event. Callers pass names and ids only, never passwords or tokens."""
    await store.record_event(
        ts=datetime.now(UTC),
        actor_id=actor.id if actor else None,
        actor_username=actor.username if actor else actor_username,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip=request.client.host if request.client else None,
        detail=detail or {},
    )


@audit_router.get("", response_model=AuditPage, summary="Security events, newest first (admin)")
async def list_events(
    store: Store,
    action: str | None = None,
    actor: Annotated[str | None, Query(description="actor username")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows, total = await store.list_events(action=action, actor=actor, limit=limit, offset=offset)
    return AuditPage(
        items=[AuditOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )
