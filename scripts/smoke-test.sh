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

# CHG-034: búsqueda unificada del directorio humanitario por el gateway
# y por el proxy del frontend, para personas y lugares de ayuda.
directory_people_query="kind=missing_person&q=demo"
directory_places_query="kind=collection_center&q=acopio"
curl --fail --silent --show-error \
  "http://127.0.0.1:${gateway_port}/api/v1/humanitarian-directory/search?${directory_people_query}" \
  | grep -q '"publicCaseCode"'
curl --fail --silent --show-error \
  "http://127.0.0.1:${frontend_port}/api/v1/humanitarian-directory/search?${directory_people_query}" \
  | grep -q '"publicCaseCode"'
curl --fail --silent --show-error \
  "http://127.0.0.1:${gateway_port}/api/v1/humanitarian-directory/search?${directory_places_query}" \
  | grep -q '"ratingsCount"'
curl --fail --silent --show-error \
  "http://127.0.0.1:${frontend_port}/api/v1/humanitarian-directory/search?${directory_places_query}" \
  | grep -q '"ratingsCount"'

# CHG-034: los aportes autenticados exigen sesión (401 sin cookie).
me_rating_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X POST -H "Idempotency-Key: smoke-idempotency-chg034" \
  "http://127.0.0.1:${gateway_port}/api/v1/me/aid-locations/44444444-4444-4444-8444-444444444404/ratings")
[ "$me_rating_status" = "401" ]
me_rating_status_proxy=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X POST -H "Idempotency-Key: smoke-idempotency-chg034" \
  "http://127.0.0.1:${frontend_port}/api/v1/me/aid-locations/44444444-4444-4444-8444-444444444404/ratings")
[ "$me_rating_status_proxy" = "401" ]

# CHG-035: la ruta de reportes de edificio está viva por el gateway y el
# proxy (422 por cabecera faltante, no 404), sin crear expedientes.
building_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X POST "http://127.0.0.1:${gateway_port}/api/v1/unverified-building-reports")
[ "$building_status" = "422" ]
building_status_proxy=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X POST "http://127.0.0.1:${frontend_port}/api/v1/unverified-building-reports")
[ "$building_status_proxy" = "422" ]

# CHG-036: consola administrativa. Sin sesión 401; user 403;
# super_admin 200 por el gateway y por el proxy del frontend.
admin_secret_file="${ADMIN_BOOTSTRAP_PASSWORD_FILE:-$HOME/.cusol-secrets/admin_password}"
if [ -f "$admin_secret_file" ]; then
  smoke_dir=$(mktemp -d)
  trap 'rm -rf "$smoke_dir"' EXIT

  admin_anon_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    "http://127.0.0.1:${gateway_port}/api/v1/admin/overview")
  [ "$admin_anon_status" = "401" ]

  # Usuario normal verificado con contraseña conocida (solo smoke local).
  docker compose -f compose.yaml exec -T identity-service python - <<'PYEOF' >/dev/null
import asyncio, uuid
import asyncpg
from app.config import Settings
from app.security import build_password_hasher

async def main():
    settings = Settings.from_environment()
    hasher = build_password_hasher(1, 8192, 1)
    connection = await asyncpg.connect(settings.database_url)
    await connection.execute(
        """
        INSERT INTO identity_service.accounts (
            id, email, first_names, last_names, department, municipality,
            requested_account_type, assigned_role, password_hash, status,
            email_verified_at, created_at, updated_at
        ) VALUES ($1, 'smoke-user@cusol.local', 'Usuaria', 'Smoke',
            'Santander', 'Bucaramanga', 'citizen', 'user', $2, 'active',
            NOW(), NOW(), NOW())
        ON CONFLICT (email) DO UPDATE SET password_hash = $2,
            status = 'active', assigned_role = 'user'
        """,
        uuid.uuid4(), hasher.hash("SmokeUser#2026aa"),
    )
    await connection.close()

asyncio.run(main())
PYEOF

  printf '{"email":"smoke-user@cusol.local","password":"SmokeUser#2026aa"}' \
    > "$smoke_dir/user-login.json"
  python3 - "$admin_secret_file" > "$smoke_dir/admin-login.json" <<'PYEOF'
import json, sys
password = open(sys.argv[1], encoding="utf-8").read().rstrip("\r\n")
print(json.dumps({"email": "admin@cusol.local", "password": password}))
PYEOF

  curl --fail --silent --show-error -c "$smoke_dir/user.jar" \
    -X POST -H "Content-Type: application/json" \
    -d @"$smoke_dir/user-login.json" \
    "http://127.0.0.1:${gateway_port}/api/v1/auth/sessions" >/dev/null
  curl --fail --silent --show-error -c "$smoke_dir/admin.jar" \
    -X POST -H "Content-Type: application/json" \
    -d @"$smoke_dir/admin-login.json" \
    "http://127.0.0.1:${gateway_port}/api/v1/auth/sessions" >/dev/null

  admin_user_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    -b "$smoke_dir/user.jar" \
    "http://127.0.0.1:${gateway_port}/api/v1/admin/overview")
  [ "$admin_user_status" = "403" ]
  admin_user_status_proxy=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    -b "$smoke_dir/user.jar" \
    "http://127.0.0.1:${frontend_port}/api/v1/admin/overview")
  [ "$admin_user_status_proxy" = "403" ]

  curl --fail --silent --show-error -b "$smoke_dir/admin.jar" \
    "http://127.0.0.1:${gateway_port}/api/v1/admin/overview" \
    | grep -q '"underReview"'
  curl --fail --silent --show-error -b "$smoke_dir/admin.jar" \
    "http://127.0.0.1:${frontend_port}/api/v1/admin/overview" \
    | grep -q '"underReview"'
  curl --fail --silent --show-error -b "$smoke_dir/admin.jar" \
    "http://127.0.0.1:${gateway_port}/api/v1/admin/submissions?limit=10" \
    | grep -q '"trackingCode"'
else
  printf '%s\n' "AVISO: sin secreto administrativo; se omite el smoke CHG-036."
fi

printf '%s\n' "Smoke test correcto: frontend, gateway, disaster-service e identity-service conectados (mapa operativo, capa humana, autenticación, directorio humanitario, reporte de edificio y consola administrativa)."
