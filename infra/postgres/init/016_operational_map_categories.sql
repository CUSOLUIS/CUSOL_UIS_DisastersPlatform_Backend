-- CHG-049 — Nuevas categorías del mapa operativo: puntos de recolección
-- y ofertas comunitarias (comida y alojamiento). Solo amplía el enum:
-- ningún dato existente cambia y los marcadores aparecerán únicamente
-- cuando una moderación autorizada proyecte ubicaciones (las ofertas
-- siguen bloqueadas por DEC-020/DEC-021 y jamás publican coordenadas
-- exactas). Idempotente: ADD VALUE IF NOT EXISTS.

ALTER TYPE disaster_service.operational_map_category
    ADD VALUE IF NOT EXISTS 'collection_point';
ALTER TYPE disaster_service.operational_map_category
    ADD VALUE IF NOT EXISTS 'community_meal';
ALTER TYPE disaster_service.operational_map_category
    ADD VALUE IF NOT EXISTS 'temporary_shelter';
