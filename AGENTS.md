# Instrucciones del backend

## Fuente de verdad

Antes de modificar comportamiento, leer:

1. `../CUSOL_UIS_DisastersPlatform_Specs/constitution.md`.
2. La especificación aplicable en `../CUSOL_UIS_DisastersPlatform_Specs/features/`.
3. El expediente aplicable en `../CUSOL_UIS_DisastersPlatform_Specs/changes/active/`.
4. `../CUSOL_UIS_DisastersPlatform_Specs/contracts/openapi.yaml`.

El backend implementa el contrato; no cambia silenciosamente tipos, estados, errores ni reglas consumidas por frontend.

## Responsabilidades

- Validar entradas, autenticación y autorización en fronteras del sistema.
- Conservar trazabilidad, fuente y vigencia de datos.
- Diseñar integraciones externas con timeout, reintentos limitados y degradación.
- Mantener separadas información oficial, reportes ciudadanos e inferencias de IA.
- Evitar que resultados de IA se publiquen como alertas oficiales autónomas.
- Este repositorio es gestionado principalmente por Claude.
- Codex no debe modificarlo salvo solicitud explícita del usuario; las necesidades detectadas desde frontend se registran en Specs y OpenAPI.

## Calidad

- Agregar pruebas unitarias, de integración y de contrato según corresponda.
- Asociar cambios y pruebas con `CHG-NNN` y criterios `AC-NNN`.
- Crear migraciones explícitas para cambios de persistencia.
- No seleccionar framework, base de datos o proveedor sin un ADR aceptado.
- Con el entorno local iniciado, verificar el autoreload del microservicio modificado y ejecutar `make smoke`; no reiniciar servicios no afectados sin necesidad.

## Git

No ejecutar `git push` ni operaciones remotas sin orden explícita del usuario. No incluir secretos, archivos de entorno ni credenciales.
