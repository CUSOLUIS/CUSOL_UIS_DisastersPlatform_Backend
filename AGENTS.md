# Instrucciones del backend

## Fuente de verdad

Antes de modificar comportamiento, leer:

1. `../CUSOL_UIS_DisastersPlatform_Specs/constitution.md`.
2. La especificación aplicable en `../CUSOL_UIS_DisastersPlatform_Specs/features/`.
3. El expediente aplicable en `../CUSOL_UIS_DisastersPlatform_Specs/changes/active/`.
4. `../CUSOL_UIS_DisastersPlatform_Specs/contracts/openapi.yaml`.

El backend implementa el contrato; no cambia silenciosamente tipos, estados, errores ni reglas consumidas por frontend.

## Grafo del repositorio (CHG-183, 2026-08-20)

`graphify-out/` contiene el grafo de conocimiento de este repositorio:
`graph.json` (datos), `GRAPH_REPORT.md` (informe), `graph.html` (mapa visual) y
`wiki/` (un artículo por comunidad).

- **Para localizar dónde vive algo, consultarlo ANTES de barrer el árbol con
  `grep` o lectura exploratoria**, desde la raíz del repositorio:
  `graphify query "<pregunta>"`, `graphify explain "<símbolo>"`,
  `graphify path "<A>" "<B>"`, `graphify affected "<archivo>"`.
  `graphify-out/wiki/index.md` es la entrada para un agente que no ejecuta nada.
- **Tras cada cambio de código**, al cerrar el expediente: `graphify update .`
  (determinista, sin LLM ni coste). Anotar el resultado en la bitácora del
  `CHG-NNN`.
- **Aviso (verificado el 2026-08-20, CHG-185):** el comando solo deja los
  archivos intactos si la topología **no** cambió. En cuanto el cambio añade
  archivos, la detección de comunidades se re-agrupa y `update` **renombra
  todas las etiquetas curadas por su nodo central** —el mismo daño que en
  Specs—. La salida anterior queda respaldada en `graphify-out/<fecha>/`. La
  reparación es determinista y está descrita en la bitácora de CHG-185:
  reasignar cada comunidad nueva a la vieja con la que más nodos comparte y
  devolverle su etiqueta en `.graphify_labels.json`, `graph.json`, `graph.html`
  y los encabezados de `GRAPH_REPORT.md`; las comunidades que el cambio
  reformó se nombran a mano. Sin esa reparación, el grafo pierde el índice en
  castellano y queda con nombres de archivo.
- El grafo es un índice, no una fuente de verdad: señala archivo y línea; lo que
  manda sigue siendo el código y el contrato.

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
