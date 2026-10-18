"""REST routes: rules CRUD and alert history."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..db import AlertRow, RuleRow
from .schemas import AlertOut, AlertPage, ErrorOut, RuleIn, RuleOut


class ApiStore(Protocol):
    async def list_rules(self) -> list[RuleRow]: ...
    async def get_rule(self, rule_id: str) -> RuleRow | None: ...
    async def create_rule(self, data: dict) -> RuleRow: ...
    async def update_rule(self, rule_id: str, data: dict) -> RuleRow | None: ...
    async def delete_rule(self, rule_id: str) -> bool: ...
    async def list_alerts(self, **filters: Any) -> tuple[list[AlertRow], int]: ...
    async def get_alert(self, alert_id: str) -> AlertRow | None: ...
    async def ack_alert(self, alert_id: str, now: datetime) -> AlertRow | None: ...


def get_store(request: Request) -> ApiStore:
    return request.app.state.store


Store = Annotated[ApiStore, Depends(get_store)]

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorOut}}
router = APIRouter(prefix="/api/v1")


async def _rules_changed(request: Request) -> None:
    """Apply changes immediately on this replica; others pick them up on their next refresh."""
    callback = getattr(request.app.state, "on_rules_changed", None)
    if callback is not None:
        await callback()


# -- rules ---------------------------------------------------------------
@router.get("/rules", response_model=list[RuleOut], tags=["rules"], summary="List rules")
async def list_rules(store: Store):
    return await store.list_rules()


@router.post(
    "/rules", response_model=RuleOut, status_code=201, tags=["rules"], summary="Create a rule"
)
async def create_rule(body: RuleIn, store: Store, request: Request):
    row = await store.create_rule(body.model_dump())
    await _rules_changed(request)
    return row


@router.get(
    "/rules/{rule_id}",
    response_model=RuleOut,
    responses=NOT_FOUND,
    tags=["rules"],
    summary="Get a rule",
)
async def get_rule(rule_id: str, store: Store):
    row = await store.get_rule(rule_id)
    if row is None:
        raise HTTPException(404, "rule not found")
    return row


@router.put(
    "/rules/{rule_id}",
    response_model=RuleOut,
    responses=NOT_FOUND,
    tags=["rules"],
    summary="Replace a rule",
)
async def replace_rule(rule_id: str, body: RuleIn, store: Store, request: Request):
    row = await store.update_rule(rule_id, body.model_dump())
    if row is None:
        raise HTTPException(404, "rule not found")
    await _rules_changed(request)
    return row


@router.delete(
    "/rules/{rule_id}",
    status_code=204,
    responses=NOT_FOUND,
    tags=["rules"],
    summary="Delete a rule (alert history is kept)",
)
async def delete_rule(rule_id: str, store: Store, request: Request):
    if not await store.delete_rule(rule_id):
        raise HTTPException(404, "rule not found")
    await _rules_changed(request)
    return Response(status_code=204)


# -- alerts --------------------------------------------------------------
@router.get(
    "/alerts",
    response_model=AlertPage,
    tags=["alerts"],
    summary="Alert history, newest first",
)
async def list_alerts(
    store: Store,
    device_id: Annotated[str | None, Query(alias="deviceId")] = None,
    severity: Literal["info", "warning", "critical"] | None = None,
    state: Literal["open", "resolved"] | None = None,
    rule_id: Annotated[str | None, Query(alias="ruleId")] = None,
    acknowledged: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows, total = await store.list_alerts(
        device_id=device_id,
        severity=severity,
        state=state,
        rule_id=rule_id,
        acknowledged=acknowledged,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return AlertPage(
        items=[AlertOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/alerts/{alert_id}",
    response_model=AlertOut,
    responses=NOT_FOUND,
    tags=["alerts"],
    summary="Get one alert",
)
async def get_alert(alert_id: str, store: Store):
    row = await store.get_alert(alert_id)
    if row is None:
        raise HTTPException(404, "alert not found")
    return row


@router.post(
    "/alerts/{alert_id}/ack",
    response_model=AlertOut,
    responses=NOT_FOUND,
    tags=["alerts"],
    summary="Acknowledge an alert (idempotent)",
)
async def ack_alert(alert_id: str, store: Store):
    row = await store.ack_alert(alert_id, datetime.now(UTC))
    if row is None:
        raise HTTPException(404, "alert not found")
    return row
