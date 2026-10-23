# Study Guide: Telemetry Anomaly Platform

A walkthrough of every component, tool and design decision in this repository, written so you can explain and defend each one in an interview or code review.

> **How to read the "why".** Where the code or comments state a reason, I quote or paraphrase it. Where they don't, I give the reasoning that the code *implies* and mark it **(inferred)**. Check those against your own memory of why you chose them, because an interviewer will ask you, not me.

---

## Table of contents

1. [The big picture](#1-the-big-picture)
2. [Core concepts you must know](#2-core-concepts-you-must-know)
3. [Cross-cutting decisions](#3-cross-cutting-decisions)
4. [Simulator (Python)](#4-simulator-python)
5. [Contracts (JSON Schema)](#5-contracts-json-schema)
6. [Ingestion (Java / Spring Boot)](#6-ingestion-java--spring-boot)
7. [Analysis (Python / FastAPI)](#7-analysis-python--fastapi)
8. [Web (React / Vite / nginx)](#8-web-react--vite--nginx)
9. [Docker and Compose](#9-docker-and-compose)
10. [Kubernetes (Kustomize / kind)](#10-kubernetes-kustomize--kind)
11. [Testing strategy](#11-testing-strategy)
12. [CI/CD (GitHub Actions)](#12-cicd-github-actions)
13. [Scripts](#13-scripts)
14. [Failure modes and delivery guarantees](#14-failure-modes-and-delivery-guarantees)
15. [Known limitations and what you'd do next](#15-known-limitations-and-what-youd-do-next)
16. [Tool index (A-Z)](#16-tool-index-a-z)
17. [Self-test questions](#17-self-test-questions)
18. [Suggested study plan](#18-suggested-study-plan)

---

## 1. The big picture

A streaming pipeline that detects anomalies in sensor data.

```
Simulator ──telemetry.raw──▶ Ingestion ──telemetry.validated──▶ Analysis ──telemetry.alerts──▶ WebSocket ──▶ Browser
 (Python)                   (Spring Boot)                        (FastAPI)                      (same process)
                              │    │                                │
                              │    └─ invalid ──▶ telemetry.dlq     └─▶ PostgreSQL (rules, alert history)
                              └─▶ MongoDB (raw readings)
```

**One reading's life:**

1. The simulator emits JSON like `{"id": "...", "deviceId": "sensor-001", "metric": "temperature", "value": 40.2, "unit": "celsius", "timestamp": "..."}` to Kafka topic `telemetry.raw`, keyed by device id.
2. Ingestion consumes a *batch*, validates each message against the JSON Schema, upserts valid ones into MongoDB (keyed by reading id), forwards them to `telemetry.validated`, and sends invalid ones to `telemetry.dlq` with a `reason` header. Only then does it commit the offset.
3. Analysis consumes `telemetry.validated`, runs the rules engine, and for each state change (open / resolved) saves the alert to PostgreSQL, then publishes it to `telemetry.alerts`. Only then does it commit the offset.
4. A broadcaster inside the analysis process consumes `telemetry.alerts` and pushes each alert to every connected WebSocket client.
5. The React page shows the live feed, and reads history and rules over REST.

**Why two databases?** Raw readings are high-volume, schemaless-ish, append-mostly, and expire (a good fit for MongoDB with a TTL index). Rules and alert history are small, relational, filtered and paginated, and need transactions and migrations (a good fit for PostgreSQL). Each store is used for what it is best at, and each service owns its own store.

**Why separate services at all?** Honestly, for a system this size one service would do. This is a portfolio project, so the split demonstrates polyglot services communicating through a broker and a shared contract. Be ready to say that plainly. The *defensible* technical reasons are independent scaling (ingestion is throughput-bound, analysis is state-bound) and failure isolation (the DB outage tests in section 14 show it working).

---

## 2. Core concepts you must know

Learn these first. Everything else builds on them.

### Kafka

- **Topic**: a named, append-only log. **Partition**: a topic is split into N ordered logs. Ordering is guaranteed *only within a partition*.
- **Key**: messages with the same key go to the same partition. This repo keys everything by **device id**, so one device's readings stay in order and one device's rule state lives on one analysis replica.
- **Consumer group**: consumers sharing a `group.id` split the partitions between them. Each message goes to one consumer in the group. Different groups each get every message.
- **Offset commit**: a consumer records "I've processed up to here". If it crashes, it resumes from the last committed offset. **Commit after processing, not before**, and you get at-least-once delivery. Commit before and you get at-most-once.
- **`auto.offset.reset`**: where to start when the group has no committed offset. `earliest` = from the beginning; `latest` = only new messages.
- **Idempotent producer** (`enable.idempotence=true`): the broker de-duplicates producer retries, so a network retry doesn't create a duplicate message. Requires `acks=all`.
- **`acks=all`**: the leader waits for all in-sync replicas before confirming. Strongest durability.
- **Dead-letter queue (DLQ)**: a topic for messages that can't be processed, so one poison message never blocks a partition.
- **KRaft mode**: Kafka without ZooKeeper (the broker is also the controller). Both the compose file and the Kubernetes manifest use it (`KAFKA_PROCESS_ROLES: broker,controller`).

### Delivery semantics

| Semantics | Meaning | How you get it |
|---|---|---|
| At-most-once | Might lose, never duplicates | Commit before processing |
| **At-least-once** | Never loses, might duplicate | Commit after processing (**this repo**) |
| Exactly-once | Neither | Hard. Kafka transactions, or at-least-once + idempotent consumers |

This repo uses **at-least-once, then makes consumers idempotent where it is cheap**. Mongo upserts by reading id, so duplicates collapse. The alert path is *not* fully idempotent (see section 15).

### Idempotency

An operation is idempotent if doing it twice has the same effect as once. Here: `upsert where _id = reading.id` is idempotent; `POST /alerts/{id}/ack` is idempotent (it only sets `acknowledged_at` if unset).

### Backpressure by stalling

When a dependency is down, the consumer *retries and does not commit*. The backlog accumulates safely in Kafka instead of being dropped or crashing the service. Kafka is the buffer.

### Pure core, impure shell

The rules engine and the simulator's generator take time and randomness as parameters (an injected clock, an injected `random.Random`) and do no I/O. The Kafka/DB code wraps them. This makes the interesting logic deterministic and trivially unit-testable. Expect to be asked about this.

---

## 3. Cross-cutting decisions

| Decision | What | Why |
|---|---|---|
| **Shared contract** | `contracts/*.schema.json` is the single source of truth. Ingestion bundles `reading.schema.json` at build time via a Maven resource; Python tests validate against the same files. | One definition of "a valid reading" for a Java service and a Python service. Never hand-copied, so it can't drift. |
| **Key by device id** | All topics use device id as the message key. | Per-device ordering; per-device rule state stays on one replica. |
| **Manual offset commit, after side effects** | Both consumers disable auto-commit. | At-least-once. A crash re-processes rather than loses. |
| **Retry forever with capped exponential backoff** | `1s → 2s → ... → 30s` (ingestion) / `0.5s → ... → 15s` (analysis pipeline). | Outages are usually temporary. Stalling is safer than dropping. The cap prevents unbounded waits. |
| **Env-var configuration with defaults** | `${KAFKA_BOOTSTRAP:localhost:9092}` etc. | 12-factor style. Same image runs locally, in Compose and in Kubernetes. Only the environment changes. |
| **Health vs readiness** | `/health` (alive) vs `/ready` (worker started). Spring: `/actuator/health/liveness` and `/readiness`. | Orchestrators need two answers: "restart me?" and "send me traffic?". |
| **camelCase on the wire, snake_case in Python** | Pydantic `alias_generator=to_camel`. | JSON/JS convention outside, Python convention inside. |
| **Non-root containers** | `USER nobody` / `USER 10001` / nginx-unprivileged. | Limits blast radius if a container is compromised. |
| **Everything tested against real dependencies where it matters** | Testcontainers for Kafka/Mongo/Postgres. | Mocks can't catch real broker or SQL behaviour. |
| **Honest documentation** | README lists known limitations and what was *not* tested. | Credibility. An interviewer trusts a candidate who states limits. |

---

## 4. Simulator (Python)

**Files:** [simulator/src/simulator/devices.py](../simulator/src/simulator/devices.py), [control.py](../simulator/src/simulator/control.py), [main.py](../simulator/src/simulator/main.py)

**Purpose:** generate realistic telemetry for fake devices and let you *inject faults on demand* so you can demonstrate every detection rule.

### Design: pure generator + thin I/O

- **`devices.py` has no I/O and no hidden clock.** `Simulator.tick(now)` returns a list of `Outgoing` messages. Time (`now`) and randomness (`rng`) are injected. Tests can seed the RNG and get exactly reproducible output.
- `main.py` is the I/O shell: connects to Kafka, calls `tick()` every second, sends the results.
- `control.py` is an HTTP API to trigger faults.

### The data model

```python
METRICS = {
  "temperature": ("celsius", 40.0, 0.4),   # unit, nominal value, noise std-dev
  "voltage":     ("volt",    28.0, 0.05),
  "pressure":    ("kPa",    101.0, 0.3),
}
```

Each value is `nominal + gauss(0, sigma)`: a stable signal with Gaussian noise. Plus a `heartbeat` metric that is a counter incrementing each tick.

### The four faults

| Fault | Effect | Rule it exercises |
|---|---|---|
| `spike` | Adds `magnitude` to one value, **once** (one-shot) | `threshold` |
| `drift` | Ramps the value up by `magnitude` over `duration_s` | `rate_of_change`, `zscore` |
| `dropout` | Device sends nothing for `duration_s` | `missing_heartbeat` |
| `malformed` | Sends `count` bad payloads (invalid JSON, missing field, wrong type, unknown metric, extra field) | Ingestion validation / DLQ |

Detail worth knowing: `spike` and `malformed` are **one-shot** (removed after being applied once); `drift` and `dropout` are **time-bounded** (expire after `duration_s`). `_expire()` and the one-shot cleanup at the end of `_tick_device` implement this.

### Kafka producer settings (`main.py`)

```python
AIOKafkaProducer(acks="all", enable_idempotence=True, linger_ms=5, request_timeout_ms=10_000)
```

- `acks="all"` + `enable_idempotence`: no loss, no duplicates from retries.
- `linger_ms=5`: wait up to 5 ms to batch messages together. Small throughput win, negligible latency cost.

### Resilience details

- **`connect()` retries with exponential backoff** (1 s → 15 s). In Compose/K8s the simulator may start before Kafka is ready. This is the line you had selected in the editor.
- **The control API starts before Kafka connects** (`asyncio.create_task(server.serve())` before `connect`). So `/health` answers immediately and the container is "alive" even while waiting for Kafka.
- **`publish_loop` compensates for work time**: `sleep(max(0, interval - elapsed))` keeps a steady 1 Hz instead of drifting.
- **Delivery callback counts `sent`/`failed`**: visible at `GET /devices`.

### Tools used

- **aiokafka**: asyncio-native Kafka client for Python.
- **FastAPI + uvicorn**: the control API. FastAPI gives validation (Pydantic) and docs for free; uvicorn is the ASGI server.
- **pytest, httpx, jsonschema, ruff**: tests, HTTP test client, contract validation, lint.
- **hatchling**: build backend in `pyproject.toml` (modern, no `setup.py`).

### Likely questions

- *Why inject the clock and RNG?* Deterministic tests; no `sleep` or flakiness.
- *Why a malformed-message fault?* To prove the DLQ path end to end, not just in unit tests.
- *Known weakness:* during a long Kafka outage the producer times out and the simulator counts those readings as failed and drops them. It's a simulator, so acceptable, and documented.

---

## 5. Contracts (JSON Schema)

**Files:** [contracts/reading.schema.json](../contracts/reading.schema.json), [alert.schema.json](../contracts/alert.schema.json)

- **JSON Schema draft 2020-12** describes the shape of messages on the wire.
- `reading.schema.json` is strict: `additionalProperties: false`, all six fields required, `deviceId` matches `^[a-z0-9][a-z0-9-]{2,63}$`, `metric` is an enum, `timestamp` must be UTC ISO-8601 ending in `Z`, `id` must look like a UUID.
- **Why `additionalProperties: false`?** Unknown fields signal a producer/consumer version mismatch. Rejecting loudly beats silently ignoring. (The simulator's `malformed` fault includes an `extra` field to prove this.)
- **Why the `id` matters:** it's the MongoDB `_id`, so it's what makes redelivery idempotent.
- **Why a UTC `Z` timestamp?** Removes timezone ambiguity between a Java and a Python service.
- `alert.schema.json` defines the shape pushed on `telemetry.alerts` and over WebSocket. Python tests validate real alert output against it.

A subtle point: the schema validates the *pattern* of the timestamp, but a string like `2024-13-45T...` can match the regex and still be nonsense. That's why `ReadingValidator` additionally runs `Instant.parse` after schema validation and returns `"timestamp is not a real date-time"`. **Two-layer validation: structure, then semantics.**

---

## 6. Ingestion (Java / Spring Boot)

**Files:** [ingestion/](../ingestion) (`pom.xml`, `application.yml`, `src/main/java/com/guimineve/telemetry/ingestion/*.java`)

**Purpose:** be the gatekeeper. Nothing unvalidated reaches the analysis service, and every raw reading is stored.

### Why Java/Spring Boot here?

**(inferred)** Ingestion is the throughput-critical, long-running, strongly-typed part, and Spring Kafka has mature batch-listener and error-handling support. It also shows a second ecosystem. Be honest if asked: the language choice is partly a portfolio decision.

### Class-by-class

| Class | Responsibility |
|---|---|
| `IngestionApplication` | Entry point. `@SpringBootApplication` + `@ConfigurationPropertiesScan`. |
| `TelemetryProperties` | A Java `record` bound to the `telemetry.*` config. Typed, immutable config instead of scattered `@Value`. |
| `ReadingValidator` | **Pure** validation. Returns a sealed `Result` = `Valid(RawReading)` or `Invalid(reason)`. |
| `RawReading` | Record for a validated reading. |
| `IngestionListener` | `@KafkaListener` that receives a **batch**, delegates to the service, then `ack.acknowledge()`. |
| `IngestionService` | Orchestrates one batch: validate, split valid/invalid, bulk-upsert, forward, DLQ, await sends. |
| `KafkaConfig` | Declares the topics (`NewTopic` beans) and the error handler with backoff. |
| `MongoIndexes` | Creates the TTL index and the query index on `ApplicationReadyEvent`. |

### Key decisions explained

**Sealed interface for results** (`sealed interface Result permits Valid, Invalid`). The compiler knows the set of cases, and the service uses pattern matching (`result instanceof Valid v`). No exceptions for control flow, no `null`.

**Batch listener + manual ack** (`listener.type: batch`, `ack-mode: manual`, `max-poll-records: 500`). Processing 500 messages at once enables one Mongo bulk write instead of 500 round trips. That's the main reason for the ~16,600 readings/s figure. Manual ack means we commit only after Mongo and Kafka sends succeed.

**`BulkOperations.UNORDERED` upsert keyed by `_id`**. Unordered lets Mongo parallelise and continue past a single failure; upsert makes redelivery idempotent: replaying the same batch changes nothing.

**Await all sends before ack** (`awaitAll`, 30 s timeout). The Kafka sends are asynchronous (`CompletableFuture`). If we acked before they completed, a crash could lose messages. Failure throws `IllegalStateException`, which the error handler retries.

**`DefaultErrorHandler` with `ExponentialBackOff(1000, 2.0)`, max 30 s, retried forever.** If Mongo is down, the whole batch fails and is retried; nothing is acked, so the backlog stays in Kafka. The comment in `KafkaConfig` spells out the key distinction: *bad messages never reach this handler (they go to the DLQ); only infrastructure failures do.*

**DLQ with diagnostic headers.** Each rejected message is sent to `telemetry.dlq` with headers `reason`, `origTopic`, `origPartition`, `origOffset`. You can find where it came from and why it failed without a debugger.

**TTL index on `ingestedAt`** (`RAW_RETENTION_DAYS`, default 7). Mongo deletes old raw data automatically. Raw telemetry grows without bound otherwise.

**Compound index `(deviceId ASC, timestamp DESC)`** supports "latest readings for device X" queries.

**Index creation failure is only a warning.** `MongoIndexes.ensure` catches errors and retries on the next start, so a transient Mongo problem at boot doesn't prevent the service from starting.

**Micrometer counters** `ingestion.readings{outcome=accepted|rejected}`, exposed through Actuator `/actuator/metrics`. `scripts/smoke.py` and `loadtest.py` read these to *measure* the system instead of guessing.

### `application.yml` highlights

- `producer.acks: all` + `enable.idempotence: true`: durability.
- `consumer.enable-auto-commit: false`, `auto-offset-reset: earliest`: no loss; a brand-new group starts from the beginning.
- `management.endpoint.health.probes.enabled: true`: turns on `/actuator/health/liveness` and `/readiness` for Kubernetes.

### `pom.xml` / tools

- **Maven + `spring-boot-starter-parent`**: dependency management (versions are aligned by the parent).
- **spring-boot-starter-webmvc**: embedded Tomcat for the Actuator endpoints (Spring Boot 4 splits starters per module).
- **spring-boot-starter-actuator**: health + metrics.
- **spring-boot-starter-data-mongodb**: `MongoTemplate`, `BulkOperations`.
- **spring-boot-starter-kafka** (Spring Kafka 4): `@KafkaListener`, `KafkaTemplate`, `DefaultErrorHandler`.
- **com.networknt:json-schema-validator**: JSON Schema validation in Java.
- **Testcontainers (kafka, mongodb, junit-jupiter) + Awaitility**: real brokers/DBs in tests; Awaitility polls for async outcomes instead of `Thread.sleep`.
- **maven-failsafe-plugin**: runs `*IT` integration tests in the `verify` phase, separate from unit tests (surefire).
- **JaCoCo**: coverage report.
- **Resource trick in `<build>`**: copies `../contracts/reading.schema.json` into the jar under `contracts/`, so there's no hand-copied schema.

### Likely questions

- *What happens if the app crashes after writing to Mongo but before the ack?* The batch is redelivered; the upsert makes it harmless. The forwarded messages to `telemetry.validated` may be duplicated downstream. That's why section 15 lists at-least-once honestly.
- *Why not process one message at a time?* Throughput. Per-message Mongo writes would be far slower.
- *Why is `MaxRAMPercentage=75` in the Dockerfile?* The JVM otherwise sizes the heap from the host's memory, not the container's limit.

---

## 7. Analysis (Python / FastAPI)

**Files:** [analysis/src/analysis/](../analysis/src/analysis)

The brain of the system: the rules engine, the Kafka worker, persistence, REST, and WebSocket.

### Module map

| Module | Role | Pure? |
|---|---|---|
| `rules/models.py` | Frozen dataclasses: `Reading`, `Rule`, `Alert` | yes |
| `rules/engine.py` | `RulesEngine`: all detection logic | **yes** (injected clock/ids) |
| `serialization.py` | Kafka JSON ↔ domain objects | yes |
| `pipeline.py` | Persist-then-publish with retries | I/O via injected protocols |
| `worker.py` | Kafka consumer/producer, 3 background loops | I/O |
| `store.py` | PostgreSQL queries (SQLAlchemy async) | I/O |
| `db.py` | Table definitions, engine/session factories | I/O |
| `migrate.py`, `migrations/` | Alembic migrations run at startup | I/O |
| `broadcast.py` | Kafka alerts → WebSocket fan-out | I/O |
| `api/routes.py`, `api/schemas.py` | REST + validation | I/O |
| `main.py` | App factory + lifespan wiring | glue |
| `config.py` | `Settings` from env vars | pure |

### The rules engine

The most important file to understand deeply: [rules/engine.py](../analysis/src/analysis/rules/engine.py).

**Four rule types**

| Type | Fires when | Params | Algorithm |
|---|---|---|---|
| `threshold` | value > `max` or < `min` | `min`, `max` | Direct comparison |
| `rate_of_change` | `|Δvalue / Δtime|` > limit | `max_rate_per_s`, `window_s` | Keep a time window of `(ts, value)`; compare newest against oldest in the window |
| `zscore` | value is > N std-devs from rolling mean | `window`, `threshold`, `min_samples` | Rolling window; compute mean/σ of the *prior* values; `z = (x − mean) / σ` |
| `missing_heartbeat` | no heartbeat for `timeout_s` | `timeout_s` | `sweep()` runs periodically and checks last-seen times |

**The state machine (the central idea)**

The engine tracks a set `_open` of `(rule_id, device_id)` pairs. `_transition()` implements:

```
violated  AND not open  → add to open, emit "open" alert
!violated AND open      → remove from open, emit "resolved" alert
otherwise               → emit nothing
```

So a *sustained* breach produces **one** alert, not one per reading (otherwise 1 Hz data would flood you). When it recovers you get a matching `resolved`.

**Cooldown (anti-flapping).** If a value hovers at the threshold, it would open/resolve/open repeatedly. `_last_opened` + `alert_cooldown_s` (default 30 s) suppresses re-opening within the cooldown window.

**Details that show care**

- **z-score uses the *prior* window**, then appends the new value. Including the new point in its own mean/σ would dampen the very outlier you want to detect.
- **z-score warm-up (`min_samples`)**: don't judge until there's enough history.
- **`std < _EPS` guard**: avoids dividing by ~zero on a perfectly flat signal.
- **The window adapts to sustained shifts** (code comment): after a level shift becomes the new normal, the z-score stops firing. That's a deliberate property of a rolling baseline.
- **Heartbeat uses *arrival* time** (the injected clock), not the device's timestamp. It answers "has *this pipeline* heard from the device recently?" Trade-off: a stalled pipeline also looks like silent devices (documented in limitations).
- **Startup grace period (`startup_grace_s`)**: right after a restart, the engine has seen no heartbeats yet. Without a grace period, every device would instantly look silent.
- **`device_pattern` with `fnmatchcase`**: shell-style wildcards (`sensor-*`) let one rule target a group of devices. Case-sensitive on purpose.
- **`set_rules()` keeps state** for rules that still exist and prunes state for deleted ones, so editing rules at runtime doesn't reset everything.
- **`seed_open()`**: after a restart, open alerts are restored from PostgreSQL so a still-breaching device doesn't re-open a duplicate alert and a recovered one still produces `resolved`.

### Pipeline: persist, then publish

[pipeline.py](../analysis/src/analysis/pipeline.py). For each batch of alerts:

1. `save_alerts` to PostgreSQL (retry until it succeeds).
2. For each alert, `publish` to Kafka (retry until it succeeds).

**Why persist first?** If the process dies after publishing but before saving, the UI would show an alert that history doesn't contain. Persisting first means the database is always at least as complete as the stream.

**Why retry separately?** So a publish failure never re-inserts rows already saved ("Persistence and publishing retry separately" per the docstring).

**Why retry forever instead of dropping?** The worker only commits the Kafka offset *after* `emit` returns. If the DB is down, the consumer *stalls*, and no alert is lost. `AlertSink` and `AlertPublisher` are `Protocol`s (structural typing), so tests inject fakes with no mocking library.

### Worker

[worker.py](../analysis/src/analysis/worker.py) runs **three asyncio tasks**:

- `_consume_loop`: `getmany(timeout_ms=1000, max_records=500)` → engine → `pipeline.emit` → `commit()`.
- `_sweep_loop`: every `sweep_interval_s` (5 s), run `engine.sweep()` for silent devices. Needed because a silent device sends *no message* to trigger evaluation.
- `_rules_loop`: every `rules_refresh_s` (5 s) reload rules from PostgreSQL, so changes made via another replica are picked up. On failure it keeps the cached rules.

Other details:

- **`ensure_topics`** creates `validated` and `alerts` topics if missing, so analysis can start before ingestion has.
- **`retry_forever`** wraps startup so the service tolerates Kafka starting late.
- **`_Reseed` rebalance listener**: when Kafka reassigns partitions (a replica joins/leaves), `on_partitions_assigned` re-loads open-alert state, because this replica now owns devices it hasn't seen before.
- **`ready` flag** is set only after everything has started; `/ready` returns 503 until then.

### Persistence

- **SQLAlchemy 2.0 async + asyncpg**: non-blocking DB access that fits the asyncio worker and FastAPI.
- **`JsonType = JSON().with_variant(JSONB(), "postgresql")`**: `JSONB` on Postgres (indexable, binary), plain `JSON` elsewhere (so tests can use other backends).
- **`alerts.rule_id` is deliberately *not* a foreign key.** Comment: *"history outlives rule deletion"*. Deleting a rule must not delete or block on its past alerts.
- **Each alert row is an *event*** (open or resolved), not a mutable record. Append-only history is simpler and auditable.
- **Three indexes** match the query patterns: `ts` (newest first), `(device_id, ts)`, `(rule_id, device_id, ts)` (also used by `open_alert_keys`).
- **`open_alert_keys` uses `DISTINCT ON`** (PostgreSQL feature) with `ORDER BY ... ts DESC` to get each pair's *latest* event, then keeps those whose state is `open`.
- **Default rules seeded with deterministic UUIDs** (`uuid5(namespace, name)`) and only if the table is empty. Re-seeding is idempotent and ids are stable across environments.
- **`pool_pre_ping=True`**: detects stale connections after a DB restart.
- **`expire_on_commit=False`**: objects stay usable after the transaction, which matters for async code that returns rows to the API layer.
- **Alembic** for schema migrations, run **programmatically at startup** over the existing async connection (`migrate.py`). Zero manual deployment step.

### REST API

[api/routes.py](../analysis/src/analysis/api/routes.py), under `/api/v1`:

| Endpoint | Purpose |
|---|---|
| `GET/POST /rules`, `GET/PUT/DELETE /rules/{id}` | Rule CRUD |
| `GET /alerts` | History with filters (`deviceId`, `severity`, `state`, `ruleId`, `acknowledged`, `since`, `until`) and `limit`/`offset` pagination, returns `{items,total,limit,offset}` |
| `GET /alerts/{id}`, `POST /alerts/{id}/ack` | One alert; acknowledge (idempotent) |

Decisions:

- **Versioned prefix `/api/v1`** so breaking changes can ship as `/v2`.
- **Dependency injection (`Depends(get_store)`) + an `ApiStore` Protocol.** Tests swap in an in-memory store without a database.
- **Strict validation** in `schemas.py`: `validate_params` rejects unknown keys per rule type, requires positive numbers, checks `min < max`, and `RuleBase._check` enforces that `missing_heartbeat` watches only `heartbeat` and other rules need a real metric. Bad rules are rejected at the door with a 422, not discovered at evaluation time.
- **`limit` capped at 200** (`le=200`) to protect the DB.
- **`_rules_changed` callback**: after create/update/delete, the local worker reloads immediately; other replicas catch up on their next 5 s refresh.
- **OpenAPI** is generated by FastAPI, exported by `export_openapi.py` to `docs/openapi.json`, and checked for drift in both a test and CI.
- **`AlertOut.timestamp` uses `validation_alias="ts"`, `serialization_alias="timestamp"`**: the DB column is `ts`; the API says `timestamp`.

### WebSocket broadcaster

[broadcast.py](../analysis/src/analysis/broadcast.py)

- Consumes `telemetry.alerts` and sends each message to all connected sockets.
- **Unique consumer group per replica** (`ws-<uuid>`) with `auto_offset_reset="latest"`. Each replica must see *every* alert (to push to *its* browsers), so replicas must **not** share a group. `latest` means a new client only gets alerts from now on; history comes via REST.
- **A dead socket must not affect others**: each `send_text` is wrapped in `try/except` and the bad client is dropped.
- **`ping`/`pong`** text frames keep connections alive through proxies and let the client detect dead links.
- **Why go through Kafka rather than broadcast directly from the worker?** It decouples replicas: any replica can produce an alert and every replica's browsers receive it.

### App factory and lifespan

[main.py](../analysis/src/analysis/main.py)

- `create_app(settings, run_worker=True)` is a **factory** (hence `uvicorn analysis.main:create_app --factory`). `run_worker=False` builds only the HTTP surface for OpenAPI export and tests.
- **`lifespan`** context manager: run migrations → seed rules → start worker → start broadcaster; on shutdown, stop in reverse order and dispose the DB engine. Clean startup/shutdown ordering.

### Users and login

Files: [auth/](../analysis/src/analysis/auth) (pure: `passwords.py`, `tokens.py`, `ratelimit.py`), [api/deps.py](../analysis/src/analysis/api/deps.py), [api/auth_routes.py](../analysis/src/analysis/api/auth_routes.py), migration `0002_users.py`.

| Decision | Why |
|---|---|
| **JWT in an `HttpOnly`, `SameSite=Strict` cookie** | The browser sends it automatically with same-origin REST calls *and* the WebSocket handshake (browsers can't set headers on a WebSocket). JavaScript can't read it, so an XSS bug can't steal it. It's stateless, so any replica can verify it without a shared session store. |
| **Token carries only the user id; the user is loaded on every request** | Disabling a user or changing their role takes effect on the next request. That fixes JWT's classic weakness (you can't revoke it) at the cost of one indexed DB read per request. |
| **argon2id** (`argon2-cffi`) | Memory-hard password hashing, the current OWASP recommendation. Hashing runs in `asyncio.to_thread` so it doesn't block the event loop. |
| **Same 401 for unknown user and wrong password, plus a dummy hash check** | Neither the message nor the response time reveals which usernames exist. |
| **Admin from `ADMIN_USERNAME`/`ADMIN_PASSWORD`, created only if absent** | No "first person to sign up owns the system" race. Never overwriting means a changed env var can't silently reset a password. `INSERT ... ON CONFLICT DO NOTHING` on the unique username makes concurrent replica startups safe. |
| **Self-registration always creates `viewer`** | The client can't choose its role. The `role` field is simply not in `RegisterIn`. |
| **Role checks as FastAPI dependencies** (`current_user`, `require_admin`) | Declared once on the router (all routes need a session) and per route (rule changes need admin). FastAPI caches a dependency within a request, so `require_admin` reuses the user `current_user` loaded. 401 = who are you? 403 = you can't do that. |
| **Admin can't demote or disable themselves** | Prevents locking the system out of its last admin by accident. |
| **WebSocket checks the Origin header** | `SameSite` doesn't reliably protect WebSocket handshakes (cross-site WebSocket hijacking). Browsers always send `Origin`; non-browser clients (the smoke test) don't, and can't ride a victim's cookie anyway. nginx forwards `Host` so the comparison works behind the proxy. |
| **In-memory sliding-window rate limiter** | 5 failed logins per minute per username and per IP. Simple, and enough for a demo. Per replica and lost on restart (documented). uvicorn trusts `X-Forwarded-For` (`FORWARDED_ALLOW_IPS`) so the limit sees the real client, not nginx. |
| **Fail fast on config** | `check_auth_settings` refuses a `JWT_SECRET` under 32 characters or half-set admin credentials at startup, rather than running insecurely. |

The frontend has an `AuthProvider` that restores the session from `/auth/me` on load, and a single `setUnauthorizedHandler` hook in `api.ts`: any non-auth request that gets 401 drops the user back to the login page.

### Tools used

- **FastAPI / Starlette**: async web framework + WebSocket support. **Pydantic v2**: validation and serialization. **uvicorn[standard]**: ASGI server (includes WebSocket support).
- **aiokafka**: consumer, producer, admin client.
- **SQLAlchemy 2 (async), asyncpg, Alembic**.
- **PyJWT, argon2-cffi**: session tokens and password hashing.
- **pytest, pytest-asyncio, httpx, jsonschema, testcontainers, ruff, mypy**.
- **mypy** with `check_untyped_defs`: static type checking in CI, which is rare for portfolio projects and worth mentioning.

---

## 8. Web (React / Vite / nginx)

**Files:** [web/](../web)

### Structure

- `App.tsx`: layout; connects the live stream; refreshes history when a new live alert arrives.
- `useAlertStream.ts`: custom hook that owns the WebSocket.
- `api.ts`: `fetch` wrappers (`fetchAlerts`, `ackAlert`, `fetchRules`) and the query builder.
- `components/`: `AlertFeed` (live), `HistoryTable` (filter + acknowledge), `RulesList`.
- `types.ts`: shared TypeScript types.

### The WebSocket hook: decisions

- **Auto-reconnect with exponential backoff**: `delay = min(1000 * 2^attempt, 15000)`. Reset `attempt` to 0 on a successful open. Without backoff a down server would be hammered.
- **A `stopped` flag + cleanup** in the `useEffect` return: closing the socket on unmount must not trigger a reconnect.
- **`mergeAlert` is a pure function**: newest first, de-duplicated by `id`, capped at 100. De-duplication matters because Kafka is at-least-once and a reconnect could replay. Being pure, it's trivial to unit test.
- **`wsUrl` picks `ws://` or `wss://`** from the page's protocol, so it works behind HTTPS without changes.
- **Ignores non-JSON frames** (`pong`) in a try/catch.
- **Same-origin URLs everywhere** (`/api/...`, `/ws/alerts`): no CORS configuration needed, because nginx (production) and the Vite proxy (dev) both route to the backend.

### Tooling

- **React 18**: UI. **TypeScript 5.9**: types; `tsc --noEmit` is the `lint` script.
- **Vite 5**: dev server and bundler. `vite.config.ts` proxies `/api` and `/ws` to the backend in dev (`ws: true` for the socket).
- **Vitest + jsdom + Testing Library**: fast unit tests with a simulated DOM, Vite-native.
- **Pinned exact versions** in `package.json` plus `package-lock.json` and `npm ci`: reproducible installs.
- **No UI/state libraries.** Only React. For this scope, `useState`/`useEffect` is enough and avoids dependency weight. A deliberate YAGNI choice.

### nginx in production

[nginx.conf.template](../web/nginx.conf.template)

- Serves the built static files; `try_files $uri /index.html` makes client-side routing work.
- **Reverse-proxies `/api/`, `/docs`, `/openapi.json`, `/ws/` to `ANALYSIS_UPSTREAM`.** One public origin, no CORS.
- The WebSocket block needs `proxy_http_version 1.1` and the `Upgrade`/`Connection` headers, or the handshake fails; `proxy_read_timeout 1h` stops nginx closing idle sockets after the 60 s default.
- `/healthz` returns 200 for orchestrator probes.
- **The `.template` extension + `ENV ANALYSIS_UPSTREAM`**: the nginx image runs `envsubst` on `/etc/nginx/templates/*.template` at start, so the same image points at `http://analysis:8000` in both Compose and Kubernetes. (Note: nginx resolves the upstream name at startup. That's why `failure_modes.py` talks to the API directly "so nginx DNS caching is not involved".)
- **`nginxinc/nginx-unprivileged`** runs as non-root on port 8080.

### Dockerfile: multi-stage

Stage 1 (`node:22-alpine`) installs deps (`npm ci`) and builds; stage 2 copies only `dist/` into nginx. The final image has no Node, no source, no `node_modules`: smaller and with a smaller attack surface. `COPY package*.json` before `COPY .` exploits **layer caching**: dependencies are re-installed only when the manifests change.

---

## 9. Docker and Compose

### Dockerfile patterns used

| Pattern | Where | Why |
|---|---|---|
| Multi-stage build | ingestion, web | Build tools stay out of the runtime image. |
| Dependency layer first | ingestion (`dependency:go-offline`), web (`npm ci`) | Cache the slow download step. |
| Build context = repo root | ingestion | The shared `/contracts` folder lives outside `ingestion/`. |
| Non-root user | all | Security hygiene. |
| `python:3.12-slim` | Python services | Small base image. |
| JRE-only runtime (`eclipse-temurin:21-jre`) | ingestion | No JDK in production. |
| `curl` installed in the ingestion image | ingestion | The Compose healthcheck uses it. |

### `docker-compose.yml`

- **Kafka in KRaft mode** with two listeners: `INTERNAL://kafka:9092` for containers and `EXTERNAL://localhost:29092` for your host. Kafka clients get told *advertised* addresses, so a container and the host need different ones. This is the classic Kafka-in-Docker gotcha, worth being able to explain.
- **`healthcheck` on every service** and **`depends_on: condition: service_healthy`**: start order follows real readiness, not just container start. `docker compose up --wait` blocks until all are healthy, which CI relies on.
- **`KAFKA_NUM_PARTITIONS: 3`**: parallelism headroom for consumers.
- **Replication factor 1** for the internal topics: single broker; fine for a demo, not for production.
- **`KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS: 0`**: no artificial startup wait for consumer groups.
- **Compose `name: telemetry`** pins the project name.

---

## 10. Kubernetes (Kustomize / kind)

**Files:** [deploy/k8s/](../deploy/k8s)

### Resource choices

| Component | Kind | Why |
|---|---|---|
| Kafka, MongoDB, PostgreSQL | **StatefulSet** + `volumeClaimTemplates` | Stable identity and persistent storage per pod. |
| Ingestion, analysis, simulator, web | **Deployment** | Stateless; replaceable. |
| Every component | **Service** | Stable DNS name (`kafka:9092`, `postgres:5432`...). |
| DB password | **Secret** (`stringData`) | Not hard-coded into Deployments; comment admits it's dev-only. |

### Details to be able to explain

- **`enableServiceLinks: false`**: Kubernetes injects env vars like `KAFKA_PORT=tcp://10.0.0.1:9092` for every Service. The Kafka image reads `KAFKA_*` env vars as config, so that breaks it (see the comment in `kafka.yaml`). Disabled on all pods for consistency.
- **initContainer `wait-for-deps`** (busybox + `nc -z`): blocks app start until Kafka and the DB accept connections. The apps also retry on their own; this makes startup quieter.
- **Three probes**:
  - `startupProbe`: gives slow starters (JVM, migrations) time without `livenessProbe` killing them.
  - `livenessProbe`: restart if hung.
  - `readinessProbe`: don't route traffic until ready (`/ready`, `/actuator/health/readiness`).
- **Resource `requests` and `limits`**: requests drive scheduling; limits cap memory. No CPU limit, which avoids CPU throttling of a bursty app.
- **`$(POSTGRES_PASSWORD)` interpolation**: Kubernetes expands earlier env vars into later ones, so `DATABASE_URL` is built from the secret.
- **`replicas: 1` on analysis with a comment**: "more replicas work: Kafka partitions the readings, each replica broadcasts all alerts". It's an explained design property, but README says multi-replica is untested.
- **Kustomize base + overlays**: `base/` is environment-neutral; `overlays/kind/` patches the `web` Service to `NodePort 30080`. A cloud overlay would patch it to a LoadBalancer/Ingress instead of copying YAML.
- **kind** ("Kubernetes IN Docker"): a throwaway real cluster inside Docker, ideal for CI. `kind-config.yaml` maps host port 3000 to NodePort 30080 so `http://localhost:3000` works.
- **kubeconform `-strict`** validates rendered manifests against Kubernetes schemas in CI, catching typos without a cluster.

### `kind-e2e.sh` flow

Create cluster → `kind load docker-image` (images are loaded straight into the node, so no registry credentials needed) → `kubectl kustomize ... | sed (rewrite image names) | kubectl apply` → `rollout status` each workload → `port-forward` → run `smoke.py` → on failure dump pods and logs → always clean up via `trap`. The `sed` is what lets the **same manifests** run against locally built images (`telemetry-web:ci`) and published ones (`ghcr.io/...:sha-abc1234`).

---

## 11. Testing strategy

The shape is a test pyramid, leaning on pure logic at the base and real infrastructure in the middle.

| Layer | What | Tools |
|---|---|---|
| **Unit, pure logic** | Rules engine: boundaries, sustained breach, per-device state, cooldown, restart restore, rate of change, z-score warm-up, heartbeat timeout, startup grace (25 tests). Simulator generation. | pytest, injected clock/RNG |
| **Unit, with fakes** | Pipeline retry behaviour (fake sink/publisher + fake `sleep`), HTTP layer (in-memory store), broadcaster. | pytest-asyncio, Protocols |
| **Contract** | Simulator output and alert JSON validated against the schemas. | jsonschema |
| **Integration (real infra)** | Ingestion: valid stored+forwarded, invalid → DLQ with reason, duplicate stored once. Analysis: migrations, SQL queries, full flow `validated → alert row + message on alerts`. | Testcontainers (Kafka, Mongo, Postgres) |
| **Frontend** | Reconnect/backoff, de-duplication, query building. | Vitest, Testing Library |
| **API drift** | Generated OpenAPI must equal the committed `docs/openapi.json`. | test + CI `diff` |
| **End to end** | Full stack: spike → exactly one alert on WebSocket and REST; malformed → rejected; dropout → silent alert opens then resolves. | `scripts/smoke.py` |
| **Chaos** | Stop each dependency and verify nothing is lost. | `scripts/failure_modes.py` |
| **Static analysis** | Lint and types. | ruff, mypy, `tsc` |

**Why Testcontainers instead of mocks?** Mocking Kafka or SQL tests your mock. Real containers catch serialization, consumer-group, SQL dialect (`DISTINCT ON`) and migration bugs. The cost is needing Docker; the pytest `integration` marker allows them to be skipped without it.

**Injected time is the key enabler**: the 25 engine tests never sleep.

**What isn't tested** (and the README says so): multiple analysis replicas, real hardware, long runs, security.

---

## 12. CI/CD (GitHub Actions)

**Files:** [.github/workflows/](../.github/workflows)

### `ci.yml`: runs on every push and PR

Parallel jobs: `simulator`, `analysis`, `web`, `ingestion`, `compose-smoke`, `k8s-validate`, `kind-e2e`; plus `images` which `needs:` the four unit-test jobs.

Notable choices:

- **`concurrency` with `cancel-in-progress`**: a newer push cancels the stale run.
- **`permissions: contents: read`**: least privilege for the token.
- **Dependency caching** (`cache: pip/npm/maven`, and `type=gha` Docker layer cache): faster runs.
- **`compose-smoke` runs `smoke.py` then `failure_modes.py`** against the real stack; dumps logs on failure and always runs `docker compose down -v`.
- **`images` matrix** builds all four images (without pushing) to catch Dockerfile breakage early.
- **`kind-e2e`** deploys to a real cluster.
- **OpenAPI drift check** in the analysis job.

### `cd.yml`: runs on push to `main` and `v*` tags

- **`publish`**: builds and pushes four images to **GHCR** (`ghcr.io/<owner>/telemetry-<name>`) with tags: `sha-<short>`, `latest` (default branch only), and semver on tags. Needs `packages: write`, granted only to that job.
- **Trivy** vulnerability scan: **reported, not blocking** (`exit-code: "0"`, `continue-on-error`). A deliberate choice not to fail the build on third-party CVEs for a demo, while still surfacing them.
- **`verify-published`**: deploys the **just-published** images to a kind cluster and runs the smoke test. This proves the *artifact* works, not merely the source.
- The comment is upfront that there is no persistent hosted environment; the ephemeral kind cluster *is* the deploy step. Be honest about that in interviews: this is "continuous delivery of verified images", not a live deployment.

### `loadtest.yml`: manual (`workflow_dispatch`)

Runs the throughput test on a GitHub-hosted runner and writes the result to the job summary. Labelled with the runner type, because numbers from different machines aren't comparable.

### `dependabot.yml`

Weekly update PRs for Maven, pip (x2), npm, Docker, and GitHub Actions. Automated dependency hygiene.

---

## 13. Scripts

| Script | What | Notable technique |
|---|---|---|
| [smoke.py](../scripts/smoke.py) | End-to-end assertions through the web container | Goes via nginx so `/api` and `/ws` proxying is also tested. `wait_until(probe, timeout)` polls instead of sleeping fixed times. Reads Actuator metrics to verify rejections. |
| [failure_modes.py](../scripts/failure_modes.py) | Stop a dependency, cause an event, restart, assert nothing was lost | Uses `docker compose stop/start` via subprocess. Talks to the API directly to avoid nginx DNS caching. |
| [loadtest.py](../scripts/loadtest.py) | Measure ingestion throughput | Pre-builds N valid messages, sends as fast as possible, then waits for the `accepted` counter to rise by N. Reports **produce rate** vs **ingest rate** (the honest one). Reports the median of several runs. |
| [kind-e2e.sh](../scripts/kind-e2e.sh) | Deploy to kind and smoke test | `set -euo pipefail`, `trap cleanup EXIT`, `KEEP_CLUSTER` flag. |

---

## 14. Failure modes and delivery guarantees

How each outage behaves, and the mechanism behind it.

| Down | Behaviour | Mechanism |
|---|---|---|
| **MongoDB** | Ingestion stalls; backlog stays in Kafka; catches up on restart | Exception from `upsertAll` → `DefaultErrorHandler` backoff → no ack |
| **Analysis service** | Readings queue in Kafka; processed after restart | Consumer group offset is only committed after processing |
| **PostgreSQL** | Alert persistence retries; the consumer stalls; REST returns errors | `AlertPipeline._retry` loops; offset not committed |
| **Kafka** | Clients reconnect; ingestion retries the batch | Client reconnect + `retry_forever` + error handler |
| **Ingestion** | Readings queue in Kafka; redelivery is idempotent | Upsert by `_id` |
| **Bad message** | To DLQ; never blocks the partition | Validation result → `sendToDlq` |

**The honest caveats** (all in the README): at-least-once means a crash between saving an alert and committing the offset can produce a *duplicate alert row*. While analysis is down there are no silent-device alerts. The simulator drops readings during a long Kafka outage.

**Why a duplicate alert row can happen:** `save_alerts` commits to Postgres, then the process crashes before `consumer.commit()`. On restart the same readings are replayed; the in-memory engine state is rebuilt only from open alerts, so the replay may re-emit an alert with a *new random id*. A fix would be deterministic alert ids (for example a hash of rule + device + triggering reading id) plus `INSERT ... ON CONFLICT DO NOTHING`. Knowing this fix is a strong interview answer.

---

## 15. Known limitations and what you'd do next

From the README, plus why each exists:

| Limitation | Why | Possible fix |
|---|---|---|
| Device that never sent a heartbeat can't be reported silent | Silence is detected relative to a *last seen* time | Maintain a device registry |
| Silent detection uses arrival time, so a stalled pipeline looks like silent devices | Deliberate (see the engine docstring) | Distinguish pipeline lag from device silence by comparing device timestamps against arrival time |
| Rate/z-score windows are in memory | Simplicity | Rebuild from a compacted topic or Redis |
| At-least-once → possible duplicate alert row | No transactional outbox | Deterministic alert ids + `ON CONFLICT DO NOTHING` |
| Single-broker Kafka, single-instance DBs, no TLS, dev passwords and JWT secret | Demo scope | Replication, mTLS/SASL, secret manager |
| Open WebSockets outlive the session until they reconnect | Checked only at the handshake | Periodic re-check on sockets, or close them when the session version changes |
| Login rate limit is per replica | Simplicity | Shared limiter in Redis or PostgreSQL |
| UI only lists rules | Scope | Add create/edit forms |
| Multi-replica analysis untested | Time | Add a test with two replicas and a rebalance |
| Load figure is from one runner, with JVM warm-up in run 1 | It's labelled as such | More runs, warm-up phase, own hardware |

---

## 16. Tool index (A-Z)

| Tool | Category | One-line purpose in this repo |
|---|---|---|
| **Actuator (Spring)** | Ops | Health probes + metrics endpoints for ingestion |
| **Alembic** | DB | Versioned schema migrations for PostgreSQL |
| **argon2-cffi** | Security | argon2id password hashing |
| **aiokafka** | Messaging | Async Kafka producer/consumer/admin for Python |
| **asyncpg** | DB | Fast async PostgreSQL driver |
| **Awaitility** | Testing | Poll for async conditions in Java tests |
| **busybox / nc** | K8s | Tiny `wait-for-deps` init container |
| **Dependabot** | Maintenance | Weekly dependency update PRs |
| **Docker / Compose** | Packaging | Containerize and run the whole stack locally |
| **docker/build-push-action, metadata-action, buildx** | CI | Build, tag and push images with layer caching |
| **FastAPI** | Web | REST + WebSocket framework (analysis, simulator control) |
| **GHCR** | Registry | Where CD publishes images |
| **GitHub Actions** | CI/CD | Test, build, publish, deploy-to-kind |
| **hatchling** | Build | Python packaging backend |
| **helm/kind-action** | CI | Installs kind on the runner |
| **httpx** | Python | HTTP client for tests and scripts |
| **JaCoCo** | Testing | Java coverage report |
| **JSON Schema (draft 2020-12)** | Contracts | Wire format definition and validation |
| **networknt json-schema-validator** | Java | Validates payloads against the schema |
| **Jackson** | Java | JSON parsing (bundled with Spring) |
| **Java 21** | Language | Records, sealed interfaces, pattern matching |
| **Kafka (KRaft, 3.9.1)** | Messaging | The backbone: durable, ordered, replayable log |
| **kind** | K8s | Disposable cluster in Docker for e2e |
| **kubeconform** | K8s | Offline manifest schema validation |
| **kubectl / Kustomize** | K8s | Apply and overlay manifests |
| **Maven (+ failsafe)** | Build | Java build; failsafe runs integration tests |
| **Micrometer** | Ops | Counters behind Actuator metrics |
| **mypy** | Quality | Static types for Python |
| **MongoDB 7** | Data | Raw readings with a TTL index |
| **nginx (unprivileged)** | Web | Static hosting + reverse proxy + WebSocket proxy |
| **npm / `npm ci`** | Build | Reproducible JS installs |
| **PostgreSQL 16** | Data | Rules and alert history |
| **PyJWT** | Security | Signs and verifies session tokens |
| **Pydantic v2** | Python | Validation, camelCase aliasing, OpenAPI models |
| **pytest / pytest-asyncio** | Testing | Python tests incl. async |
| **React 18** | UI | Components and hooks |
| **ruff** | Quality | Lint (E, F, I, UP, B rules) |
| **Spring Boot 4.1 / Spring Kafka / Spring Data MongoDB** | Java | Framework, listener containers, Mongo templates |
| **SQLAlchemy 2 (async)** | DB | ORM / query builder |
| **Testcontainers** | Testing | Real Kafka/Mongo/Postgres in tests |
| **Trivy** | Security | Image vulnerability scanning (non-blocking) |
| **TypeScript** | Language | Typed frontend |
| **uvicorn** | Python | ASGI server |
| **Vite / Vitest / jsdom / Testing Library** | Frontend | Dev server, bundler, tests |
| **WebSocket** | Protocol | Push alerts to the browser |

---

## 17. Self-test questions

Try to answer without looking. Section numbers point to the answer.

1. Why key every Kafka topic by device id? (§2, §3)
2. What does "at-least-once" mean here and where exactly is the commit placed? (§2, §6, §7)
3. How does the system avoid storing the same reading twice? (§5, §6)
4. A message arrives with an extra JSON field. What happens, step by step? (§5, §6)
5. MongoDB goes down for 60 s. What happens to messages, and what does the operator see? (§14)
6. Why does the rules engine take a `clock` parameter?
7. Why does a sustained breach produce one alert instead of many? What is the cooldown for? (§7)
8. Why compute the z-score against the prior window, not including the new value? (§7)
9. Why does `missing_heartbeat` need a periodic `sweep()` and a startup grace? (§7)
10. Why is `alerts.rule_id` not a foreign key? (§7)
11. Why does each analysis replica use a *unique* consumer group for the broadcaster, but a *shared* one for the worker? (§7)
12. How can a duplicate alert row happen and how would you eliminate it? (§14)
13. Why does the nginx config need `Upgrade`/`Connection` headers and a long read timeout? (§8)
14. Why does the Kafka container need two listeners in Compose? (§9)
15. What does `enableServiceLinks: false` fix? (§10)
16. What's the difference between startup, liveness and readiness probes? (§10)
17. Why does CD deploy the published images to kind? What does it prove and not prove? (§12)
18. Why Testcontainers over mocks? What does it cost? (§11)
19. What would break if `additionalProperties` were `true` in the reading schema? (§5)
20. Which parts of this system would you change first before calling it production-ready? (§15)
21. Why put the session in an HttpOnly cookie rather than localStorage, and why check Origin on the WebSocket? (§7)
22. A JWT can't be revoked. How does this app still make disabling a user take effect immediately? (§7)

---

## 18. Suggested study plan

**Day 1: foundations.** Read §1-3. Read the [README](../README.md). Run `docker compose up --build --wait`, open `http://localhost:3000`, and inject a spike, a dropout and a malformed fault with the curl commands. Watch each appear.

**Day 2: the core logic.** Read [engine.py](../analysis/src/analysis/rules/engine.py) and its tests ([test_engine.py](../analysis/tests/test_engine.py)). Re-derive the state machine on paper. Change a rule through the REST API and watch behaviour change.

**Day 3: the data path.** Read the ingestion Java classes in the order: `ReadingValidator` → `IngestionService` → `KafkaConfig`. Then [worker.py](../analysis/src/analysis/worker.py) and [pipeline.py](../analysis/src/analysis/pipeline.py). Trace the at-least-once commit points in both services.

**Day 4: failure.** Read [failure_modes.py](../scripts/failure_modes.py). Run `docker compose stop mongo` yourself, inject traffic, restart it, and watch ingestion catch up in the logs.

**Day 5: delivery.** Read both Dockerfiles, `docker-compose.yml`, the K8s manifests, and the three workflows. Run `scripts/kind-e2e.sh` locally with `KEEP_CLUSTER=1`.

**Day 6: frontend + API.** Read `useAlertStream.ts`, the nginx template, and `docs/openapi.json` alongside `api/routes.py` and `schemas.py`.

**Day 7: rehearse.** Answer §17 out loud. Then pick the three limitations from §15 you'd fix and sketch the change for each. Being able to say *what's wrong with your own system and how to fix it* is the strongest thing you can bring to an interview.
