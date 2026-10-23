#!/usr/bin/env bash
# Deploy the stack to a throwaway kind cluster and run the end-to-end smoke test against it.
#
#   scripts/kind-e2e.sh <image-prefix> <tag>
#   e.g. scripts/kind-e2e.sh telemetry- ci                          (images built locally)
#        scripts/kind-e2e.sh ghcr.io/guimineve/telemetry- sha-abc1234 (published images)
#
# Images are pulled on the host if absent, then loaded into the cluster, so no registry
# credentials are needed inside kind. Set KEEP_CLUSTER=1 to leave the cluster up.
set -euo pipefail

PREFIX=${1:?image prefix}
TAG=${2:?image tag}
CLUSTER=${CLUSTER:-telemetry}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PIDS=()

cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  if [ "${KEEP_CLUSTER:-0}" != 1 ]; then kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
kind create cluster --name "$CLUSTER" --config "$ROOT/deploy/k8s/overlays/kind/kind-config.yaml" --wait 120s

for svc in simulator ingestion analysis web; do
  image="${PREFIX}${svc}:${TAG}"
  docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
  kind load docker-image "$image" --name "$CLUSTER"
done

kubectl kustomize "$ROOT/deploy/k8s/overlays/kind" \
  | sed -E "s#image: telemetry-(simulator|ingestion|analysis|web):dev#image: ${PREFIX}\1:${TAG}#" \
  | kubectl apply -f -

echo "waiting for workloads"
for sts in kafka mongo postgres; do
  kubectl -n telemetry rollout status statefulset/$sts --timeout=300s
done
for dep in ingestion analysis simulator web; do
  kubectl -n telemetry rollout status deployment/$dep --timeout=300s
done

kubectl -n telemetry port-forward svc/web 3000:80 >/dev/null 2>&1 & PIDS+=($!)
kubectl -n telemetry port-forward svc/simulator 8001:8001 >/dev/null 2>&1 & PIDS+=($!)
kubectl -n telemetry port-forward deploy/ingestion 8081:8081 >/dev/null 2>&1 & PIDS+=($!)
sleep 3

probe() {  # probe <labels> <host> <port>: can a pod with these labels open the connection?
  kubectl -n telemetry run "np-probe-$RANDOM" --image=busybox:1.36 --restart=Never --rm -i --quiet \
    --labels="$1" --command -- nc -z -w 5 "$2" "$3" >/dev/null 2>&1
}

check_network_policies() {
  echo "checking network policies"
  # a pod labelled like web may reach analysis, but not the databases or Kafka
  probe app=web analysis 8000 || { echo "FAIL: web-labelled pod cannot reach analysis"; return 1; }
  for target in "postgres 5432" "mongo 27017" "kafka 9092"; do
    if probe app=web $target; then echo "FAIL: web-labelled pod reached $target"; return 1; fi
  done
  # an unlabelled pod gets nothing, not even analysis
  if probe app=intruder analysis 8000; then echo "FAIL: unlabelled pod reached analysis"; return 1; fi
  echo "network policies enforced"
}

{ python3 "$ROOT/scripts/smoke.py" && check_network_policies; } || {
  kubectl -n telemetry get pods -o wide
  for app in ingestion analysis simulator kafka; do
    echo "--- logs: $app"; kubectl -n telemetry logs -l app=$app --tail=80 --all-containers || true
  done
  exit 1
}
