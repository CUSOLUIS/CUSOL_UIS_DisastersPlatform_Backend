"""Almacenamiento de objetos con referencias opacas (CHG-007).

Los binarios nunca viven en la base transaccional ni conservan el
nombre original del cliente: la clave es una ruta derivada de UUIDs.
La interfaz permite sustituir el backend local por un servicio de
objetos gestionado sin tocar el resto del código.
"""

from pathlib import Path
from typing import Protocol


class ObjectStorage(Protocol):
    def save(self, key: str, data: bytes) -> None: ...

    def delete(self, key: str) -> None: ...


class StorageUnavailableError(Exception):
    """El backend de objetos no está disponible."""


class LocalObjectStorage:
    """Backend local sobre un volumen dedicado, con escritura atómica."""

    def __init__(self, base_dir: str):
        self._base = Path(base_dir)

    def _path(self, key: str) -> Path:
        path = (self._base / key).resolve()
        if not path.is_relative_to(self._base.resolve()):
            raise StorageUnavailableError("Clave de objeto inválida.")
        return path

    def save(self, key: str, data: bytes) -> None:
        try:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError as error:
            raise StorageUnavailableError(
                "No fue posible almacenar el archivo."
            ) from error

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError:  # la limpieza no debe ocultar el error original
            pass
