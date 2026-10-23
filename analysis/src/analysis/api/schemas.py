"""Request/response models for the REST API (camelCase on the wire)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

Metric = Literal["temperature", "voltage", "pressure", "heartbeat"]
RuleTypeName = Literal["threshold", "rate_of_change", "missing_heartbeat", "zscore"]
SeverityName = Literal["info", "warning", "critical"]


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


def _positive(params: dict[str, Any], key: str, default: float | None = None) -> float:
    value = params.get(key, default)
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"params.{key} must be a positive number")
    return float(value)


def validate_params(rule_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """Check the params for a rule type and reject unknown keys."""
    allowed = {
        "threshold": {"min", "max"},
        "rate_of_change": {"max_rate_per_s", "window_s"},
        "missing_heartbeat": {"timeout_s"},
        "zscore": {"window", "threshold", "min_samples"},
    }[rule_type]
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f"unknown params for {rule_type}: {sorted(unknown)}")
    if rule_type == "threshold":
        lo, hi = params.get("min"), params.get("max")
        if lo is None and hi is None:
            raise ValueError("threshold needs params.min and/or params.max")
        for k in ("min", "max"):
            if k in params and (
                not isinstance(params[k], int | float) or isinstance(params[k], bool)
            ):
                raise ValueError(f"params.{k} must be a number")
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError("params.min must be less than params.max")
    elif rule_type == "rate_of_change":
        _positive(params, "max_rate_per_s")
        _positive(params, "window_s", 10)
    elif rule_type == "missing_heartbeat":
        _positive(params, "timeout_s")
    elif rule_type == "zscore":
        _positive(params, "threshold", 3.0)
        window = int(_positive(params, "window", 60))
        min_samples = int(_positive(params, "min_samples", 20))
        if min_samples < 2 or min_samples > window:
            raise ValueError("params.min_samples must be between 2 and params.window")
    return params


class RuleBase(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    type: RuleTypeName
    metric: Metric | None = None
    device_pattern: str = Field(default="*", min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    severity: SeverityName = "warning"
    enabled: bool = True

    @model_validator(mode="after")
    def _check(self) -> RuleBase:
        if self.type == "missing_heartbeat":
            if self.metric not in (None, "heartbeat"):
                raise ValueError("missing_heartbeat rules watch the heartbeat metric only")
        elif self.metric is None or self.metric == "heartbeat":
            raise ValueError(f"{self.type} rules need a measurement metric")
        validate_params(self.type, self.params)
        return self


class RuleIn(RuleBase):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "name": "Temperature too high",
                    "type": "threshold",
                    "metric": "temperature",
                    "devicePattern": "sensor-*",
                    "params": {"max": 60},
                    "severity": "critical",
                    "enabled": True,
                }
            ]
        },
    )


class RuleOut(RuleBase):
    id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AlertOut(ApiModel):
    id: str
    rule_id: str
    rule_name: str
    rule_type: RuleTypeName
    device_id: str
    metric: str | None
    severity: SeverityName
    state: Literal["open", "resolved"]
    message: str
    value: float | None
    timestamp: datetime = Field(validation_alias="ts", serialization_alias="timestamp")
    acknowledged_at: datetime | None = None


class AlertPage(ApiModel):
    items: list[AlertOut]
    total: int
    limit: int
    offset: int


class ErrorOut(BaseModel):
    detail: str


# -- auth and users --------------------------------------------------------
Role = Literal["admin", "viewer"]
USERNAME_PATTERN = r"^[a-z0-9_.-]{3,32}$"


class _Lowercase(ApiModel):
    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("username"), str):
            return {**data, "username": data["username"].strip().lower()}
        return data


class RegisterIn(_Lowercase):
    username: str = Field(pattern=USERNAME_PATTERN, description="3-32 of a-z 0-9 _ . -")
    password: str = Field(min_length=10, max_length=128)


class LoginIn(_Lowercase):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class UserOut(ApiModel):
    id: str
    username: str
    role: Role
    is_active: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class PasswordChangeIn(ApiModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class PasswordResetIn(ApiModel):
    new_password: str = Field(min_length=10, max_length=128)


class UserPatch(ApiModel):
    role: Role | None = None
    is_active: bool | None = None
