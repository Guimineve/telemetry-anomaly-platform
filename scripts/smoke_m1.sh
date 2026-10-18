#!/usr/bin/env bash
# Milestone-1 smoke: a spike and a malformed payload flow through Kafka -> ingestion -> Mongo / DLQ.
# Replaced by scripts/smoke.py once the analysis service exists.
set -euo pipefail
curl -fsS -X POST localhost:8001/faults -H 'content-type: application/json' \
  -d '{"kind":"malformed","deviceId":"sensor-001","count":3}'
sleep 8
docker compose exec -T mongo mongosh --quiet telemetry --eval 'db.readings.countDocuments({})' | tee /dev/stderr | grep -qE '^[1-9]'
docker compose exec -T kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic telemetry.dlq --from-beginning --timeout-ms 5000 2>/dev/null | grep -q .
echo "m1 smoke ok"
