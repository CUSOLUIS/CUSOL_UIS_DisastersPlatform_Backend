# CUSOL UIS Disasters Platform — Backend

Backend inicial en microservicios para la plataforma de desastres naturales. La arquitectura y sus motivos están en `../CUSOL_UIS_DisastersPlatform_Specs/architecture/`.

## Servicios iniciales

| Servicio | Responsabilidad | Acceso local |
| --- | --- | --- |
| `api-gateway` | Contrato HTTP público y enrutamiento | <http://localhost:8000> |
| `disaster-service` | Eventos y reglas del dominio | Solo red interna |
| `disasters-db` | PostgreSQL/PostGIS del dominio | Solo red interna |
| `frontend` | Aplicación web y proxy de API | <http://localhost:3100> |

La base y `disaster-service` no publican puertos al host. El gateway es la única frontera pública del backend.
La imagen de PostgreSQL/PostGIS se construye localmente sobre la imagen oficial multi-arquitectura de PostgreSQL, por lo que funciona en AMD64 y ARM64.

## Requisitos

- Docker Engine.
- Docker Compose.

Node y Python solo son necesarios si se desea desarrollar fuera de los contenedores.

## Inicio rápido

```bash
cp .env.example .env
make dev
make smoke
```

Abrir:

- Aplicación: <http://localhost:3100>
- API Gateway: <http://localhost:8000>
- Swagger del gateway: <http://localhost:8000/docs>

La contraseña incluida es exclusivamente para desarrollo local. Cambiarla en `.env`; este archivo está ignorado por Git.

## Recarga automática

`make dev` inicia el entorno en segundo plano y espera que todos los servicios estén saludables.

- Cambios en `Frontend/src` o en otros archivos del frontend montado activan Vite HMR en <http://localhost:3100>.
- Cambios en `services/api-gateway/app` recargan únicamente el gateway.
- Cambios en `services/disaster-service/app` recargan únicamente el servicio de desastres.
- La base de datos y los demás contenedores no se reinician por cambios de código.

Después de cambiar dependencias, Dockerfiles, `compose.yaml` o scripts de inicialización, ejecutar nuevamente `make dev` para reconstruir lo necesario.

## Comandos

```bash
make config  # valida compose.yaml
make build   # construye imágenes
make dev     # inicia desarrollo, HMR y autoreload
make up      # alias de inicio del entorno local
make ps      # muestra salud y puertos
make logs    # sigue registros
make logs-frontend # sigue Vite/HMR
make logs-backend  # sigue recargas de FastAPI
make test    # pruebas unitarias de ambos servicios
make migrate # aplica los esquemas de infra/postgres/init a una base existente
make seed    # carga datos semilla sintéticos en la base local
make smoke   # prueba la vertical por HTTP
make down    # detiene contenedores, conserva datos
make clean   # detiene y elimina también el volumen local
```

`make clean` elimina de forma irreversible la base de desarrollo almacenada en el volumen `cusol-disasters-data`.

## Datos semilla (CHG-002, CHG-003)

La base arranca vacía. Para desarrollar contra datos visibles:

```bash
make seed
```

Aplica todos los scripts de `infra/postgres/seed/`, **sintéticos** (inspirados en eventos y entidades colombianas, sin valor oficial) e idempotentes: re-ejecutarlos no duplica datos.

Los archivos tienen prefijo numérico porque se aplican en orden alfabético y hay dependencias entre ellos:

- `10_dev_seed_events.sql` (CHG-002): 7 eventos con tipos variados, los cuatro estados de verificación, fuentes `official`/`citizen`/`ai_inference` y un evento sin coordenadas.
- `20_dev_seed_people.sql` (CHG-003): 12 personas ficticias con los cuatro estados humanos para `GET /api/v1/people/overview`.
- `30_dev_seed_operational_map.sql` (CHG-006): 8 puntos operativos (2 por categoría) para `GET /api/v1/operational-map/overview`, sin identidad en `missing_person` y con precisión no exacta (DEC-007).
- `40_dev_seed_missing_persons.sql` (CHG-007): 3 casos públicos `published` y 1 `under_review` (no indexado) para `GET /api/v1/missing-persons/search`.

## Reportes de personas desaparecidas (CHG-007)

`POST /api/v1/missing-person-reports` recibe multipart (`payload` JSON + 1–5 `photos`) con encabezado `Idempotency-Key`:

- El tipo de cada foto se valida por contenido real (magic bytes), no por extensión ni MIME del cliente; 10 MiB por archivo y 50 MiB totales.
- Los archivos pasan análisis de firmas de malware y se re-codifican con Pillow, lo que elimina EXIF; se almacenan bajo claves opacas en el volumen `cusol-disasters-report-uploads`.
- Documento, información médica y contactos del reportante se cifran (Fernet, clave `REPORT_ENCRYPTION_KEY`) antes de persistirse; no aparecen en logs ni en respuestas.
- El escáner local es de firmas (EICAR y marcadores de ejecutables); producción debe sustituirlo por un motor completo (p. ej. ClamAV) mediante la interfaz `MalwareScanner`.
- El gateway aplica rate limiting por origen (`SEARCH_RATE_LIMIT_PER_MINUTE`, `REPORTS_RATE_LIMIT_PER_MINUTE`).

Si la base ya existía antes de un nuevo script de esquema, ejecutar primero `make migrate` (los scripts de `init/` solo corren automáticamente en un volumen nuevo). Para volver al estado vacío, `make clean` y `make dev`.

## Estructura

```text
.
├── compose.yaml
├── infra/
│   └── postgres/
│       ├── init/
│       └── seed/
├── scripts/
└── services/
    ├── api-gateway/
    │   ├── app/
    │   └── tests/
    └── disaster-service/
        ├── app/
        └── tests/
```

Cada servicio tiene dependencias y Dockerfile propios. No se permite importar código directamente entre servicios; se comunican por contratos.
Los Dockerfiles conservan targets separados `development`, `test` y `runtime` para evitar habilitar recarga automática en una imagen de ejecución.

## Configuración

| Variable | Valor predeterminado | Uso |
| --- | --- | --- |
| `FRONTEND_PORT` | `3100` | Puerto web del host |
| `GATEWAY_PORT` | `8000` | Puerto del gateway |
| `POSTGRES_DB` | `disasters` | Base local |
| `POSTGRES_USER` | `disasters` | Usuario local |
| `POSTGRES_PASSWORD` | `disasters-local-only` | Contraseña local |
| `REPORT_ENCRYPTION_KEY` | `dev-local-only-report-key` | Cifrado de campos privados de reportes |
| `SEARCH_RATE_LIMIT_PER_MINUTE` | `60` | Límite de búsquedas por origen |
| `REPORTS_RATE_LIMIT_PER_MINUTE` | `10` | Límite de reportes por origen |

No utilizar estos valores en producción.

## Nuevos microservicios

Un nuevo servicio requiere:

1. Una funcionalidad aprobada y un límite de dominio claro.
2. ADR o actualización arquitectónica.
3. Contrato y propietario de datos.
4. Health checks, timeouts y pruebas.
5. Servicio en Compose sin publicar puertos internos innecesarios.
