#!/usr/bin/env python3
"""Ingestion throughput test against a running `docker compose up` stack.

Publishes N pre-built valid readings to `telemetry.raw` as fast as possible, then waits until the
ingestion service's `ingestion.readings{outcome=accepted}` counter has advanced by N. Reports:

  produce_rate    readings/s the producer pushed into Kafka
  ingest_rate     readings/s from the first send until ingestion had validated, stored in
                  MongoDB and forwarded all N (the figure to quote)

This is a single-node, single-partition-consumer-group measurement on whatever machine runs it,
with the analysis service consuming the same stream. It is not a benchmark.

Requires: pip install aiokafka httpx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import time
import uuid
from datetime import UTC, datetime

import httpx
from aiokafka import AIOKafkaProducer

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:29092")
INGEST = os.getenv("INGEST_URL", "http://localhost:8080")
TOPIC = os.getenv("TOPIC_RAW", "telemetry.raw")
METRICS = [
    ("temperature", "celsius", 40.0),
    ("voltage", "volt", 28.0),
    ("pressure", "kPa", 101.0),
]


def build(n: int, devices: int) -> list[tuple[bytes, bytes]]:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    out = []
    for i in range(n):
        device = f"load-{i % devices:04d}"
        metric, unit, base = METRICS[i % len(METRICS)]
        doc = {
            "id": str(uuid.uuid4()),
            "deviceId": device,
            "metric": metric,
            "value": round(base + (i % 7) * 0.01, 3),
            "unit": unit,
            "timestamp": ts,
        }
        out.append((device.encode(), json.dumps(doc).encode()))
    return out


async def accepted(http: httpx.AsyncClient) -> float:
    r = await http.get(
        f"{INGEST}/actuator/metrics/ingestion.readings",
        params={"tag": "outcome:accepted"},
    )
    r.raise_for_status()
    return r.json()["measurements"][0]["value"]


async def run_once(n: int, devices: int, timeout_s: float) -> dict:
    messages = build(n, devices)
    async with httpx.AsyncClient(timeout=10) as http:
        start_count = await accepted(http)
        producer = AIOKafkaProducer(
            bootstrap_servers=BOOTSTRAP, linger_ms=20, max_batch_size=256 * 1024, acks=1
        )
        await producer.start()
        try:
            t0 = time.perf_counter()
            for key, value in messages:
                await producer.send(TOPIC, value=value, key=key)
            await producer.flush()
            t_produced = time.perf_counter() - t0
        finally:
            await producer.stop()

        deadline = time.perf_counter() + timeout_s
        while await accepted(http) - start_count < n:
            if time.perf_counter() > deadline:
                raise SystemExit(
                    f"timed out: ingestion advanced {await accepted(http) - start_count} of {n}"
                )
            await asyncio.sleep(0.25)
        t_total = time.perf_counter() - t0
    return {
        "n": n,
        "produce_s": t_produced,
        "total_s": t_total,
        "produce_rate": n / t_produced,
        "ingest_rate": n / t_total,
    }


def cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=100_000, help="readings per run")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--devices", type=int, default=200)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--label", default=os.getenv("LOADTEST_LABEL", "local machine"))
    args = ap.parse_args()

    runs = []
    for i in range(args.runs):
        r = await run_once(args.n, args.devices, args.timeout)
        runs.append(r)
        print(
            f"run {i + 1}: {r['n']} readings, produced in {r['produce_s']:.1f}s "
            f"({r['produce_rate']:.0f}/s), fully ingested in {r['total_s']:.1f}s "
            f"({r['ingest_rate']:.0f}/s)",
            flush=True,
        )

    rates = sorted(r["ingest_rate"] for r in runs)
    print()
    print(f"### Ingestion throughput ({args.label})")
    print(f"- date: {datetime.now(UTC):%Y-%m-%d}")
    print(
        f"- machine: {cpu_model()}, {os.cpu_count()} logical CPUs, {platform.system()} "
        f"{platform.release()}"
    )
    print(
        f"- {args.runs} runs of {args.n} readings, {args.devices} devices, "
        f"single-broker Kafka (3 partitions), MongoDB 7, analysis service consuming"
    )
    print(
        f"- ingest rate: min {rates[0]:.0f}, median {rates[len(rates) // 2]:.0f}, "
        f"max {rates[-1]:.0f} readings/s"
    )


if __name__ == "__main__":
    asyncio.run(main())
