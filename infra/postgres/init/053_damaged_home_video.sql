-- CHG-201 — «Mi casita destruida»: enlace opcional a un vídeo de TikTok.
--
-- Una casa destruida se entiende mejor en movimiento que en tres fotos
-- fijas, y mucha gente ya grabó lo que le pasó. El enlace es público,
-- como las fotos y como el resto de la ficha.
--
-- La columna guarda una URL ya validada por el servicio contra una
-- lista cerrada de anfitriones de TikTok y exigiendo https
-- (DEC-201-01): sin esa puerta, el campo sería un canal para publicar
-- cualquier enlace junto a un medio para recibir dinero.
--
-- Idempotente; no toca ninguna fila existente. Las casitas publicadas
-- antes quedan con NULL, que es exactamente «no hay vídeo».

ALTER TABLE disaster_service.damaged_home_reports
  ADD COLUMN IF NOT EXISTS video_url TEXT;

COMMENT ON COLUMN disaster_service.damaged_home_reports.video_url IS
  'CHG-201 — Enlace público a un vídeo de TikTok, validado contra una lista cerrada de anfitriones. NULL = sin vídeo.';
