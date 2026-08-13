#!/usr/bin/env sh
set -eu

gateway_port="${GATEWAY_PORT:-8000}"
frontend_port="${FRONTEND_PORT:-3100}"

curl --fail --silent --show-error \
  "http://127.0.0.1:${gateway_port}/health/ready" >/dev/null
curl --fail --silent --show-error \
  "http://127.0.0.1:${gateway_port}/api/v1/disasters" >/dev/null
curl --fail --silent --show-error \
  "http://127.0.0.1:${frontend_port}/" >/dev/null
curl --fail --silent --show-error \
  "http://127.0.0.1:${frontend_port}/api/v1/disasters" >/dev/null

# CHG-010: el resumen del mapa operativo debe incluir buildingPending
# tanto por el gateway como por el proxy del frontend.
curl --fail --silent --show-error \
  "http://127.0.0.1:${gateway_port}/api/v1/operational-map/overview" \
  | grep -q '"buildingPending"'
curl --fail --silent --show-error \
  "http://127.0.0.1:${frontend_port}/api/v1/operational-map/overview" \
  | grep -q '"buildingPending"'

printf '%s\n' "Smoke test correcto: frontend, gateway y disaster-service conectados (mapa operativo con buildingPending)."
