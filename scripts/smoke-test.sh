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

# CHG-015: la capa de situación humana responde con totales por el
# gateway y por el proxy del frontend.
human_map_query="west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=5"
curl --fail --silent --show-error \
  "http://127.0.0.1:${gateway_port}/api/v1/people/map-overview?${human_map_query}" \
  | grep -q '"totalMapped"'
curl --fail --silent --show-error \
  "http://127.0.0.1:${frontend_port}/api/v1/people/map-overview?${human_map_query}" \
  | grep -q '"totalMapped"'

# CHG-022: registro (202 anti-enumeración, re-ejecutable) y sesión ausente
# (401) por el gateway y por el proxy del frontend.
auth_payload='{"firstNames":"Smoke","lastNames":"Test","email":"smoke-test@cusol.local","department":"Santander","municipality":"Bucaramanga","requestedAccountType":"citizen","password":"SmokeTest#2026aa","termsAccepted":true,"privacyAccepted":true,"accuracyConfirmed":true}'
curl --fail --silent --show-error \
  -X POST -H "Content-Type: application/json" -d "$auth_payload" \
  "http://127.0.0.1:${gateway_port}/api/v1/auth/registrations" \
  | grep -q '"emailMasked"'
curl --fail --silent --show-error \
  -X POST -H "Content-Type: application/json" -d "$auth_payload" \
  "http://127.0.0.1:${frontend_port}/api/v1/auth/registrations" \
  | grep -q '"emailMasked"'
me_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:${gateway_port}/api/v1/auth/me")
[ "$me_status" = "401" ]
me_status_proxy=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:${frontend_port}/api/v1/auth/me")
[ "$me_status_proxy" = "401" ]

printf '%s\n' "Smoke test correcto: frontend, gateway, disaster-service e identity-service conectados (mapa operativo, capa humana y autenticación)."
