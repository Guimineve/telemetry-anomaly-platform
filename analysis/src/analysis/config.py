from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+asyncpg://telemetry:telemetry@localhost:5432/telemetry"
    kafka_bootstrap: str = "localhost:9092"
    topic_validated: str = "telemetry.validated"
    topic_alerts: str = "telemetry.alerts"
    topic_partitions: int = 3
    consumer_group: str = "analysis"
    alert_cooldown_s: float = 30.0
    startup_grace_s: float = 20.0
    sweep_interval_s: float = 5.0
    rules_refresh_s: float = 5.0
    seed_default_rules: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            database_url=os.getenv("DATABASE_URL", d.database_url),
            kafka_bootstrap=os.getenv("KAFKA_BOOTSTRAP", d.kafka_bootstrap),
            topic_validated=os.getenv("TOPIC_VALIDATED", d.topic_validated),
            topic_alerts=os.getenv("TOPIC_ALERTS", d.topic_alerts),
            topic_partitions=int(os.getenv("TOPIC_PARTITIONS", d.topic_partitions)),
            consumer_group=os.getenv("CONSUMER_GROUP", d.consumer_group),
            alert_cooldown_s=float(os.getenv("ALERT_COOLDOWN_S", d.alert_cooldown_s)),
            startup_grace_s=float(os.getenv("STARTUP_GRACE_S", d.startup_grace_s)),
            sweep_interval_s=float(os.getenv("SWEEP_INTERVAL_S", d.sweep_interval_s)),
            rules_refresh_s=float(os.getenv("RULES_REFRESH_S", d.rules_refresh_s)),
            seed_default_rules=_bool("SEED_DEFAULT_RULES", d.seed_default_rules),
        )
