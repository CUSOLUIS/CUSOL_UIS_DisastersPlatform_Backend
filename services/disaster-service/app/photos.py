"""Validación y saneamiento de fotografías (CHG-007).

Nada de lo que envía el cliente se toma como confiable: el tipo se
determina por el contenido real (magic bytes), el archivo se analiza
contra firmas de malware y todo derivado se re-codifica con Pillow,
lo que elimina EXIF y cualquier metadato incrustado.
"""

from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

from PIL import Image, ImageOps

try:  # HEIC/HEIF requiere pillow-heif; se degrada de forma explícita.
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - entorno sin pillow-heif
    HEIF_SUPPORTED = False


ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

_HEIF_BRANDS = {
    b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis",
    b"hevm", b"hevs", b"mif1", b"msf1", b"heif",
}


def sniff_image_type(data: bytes) -> str | None:
    """Detecta el tipo real por magic bytes; None si no está permitido."""
    if len(data) < 16:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in _HEIF_BRANDS:
            return "image/heif" if brand in {b"mif1", b"msf1", b"heif"} else "image/heic"
    return None


class MalwareScanner(Protocol):
    def scan(self, data: bytes) -> bool:
        """True si el contenido está limpio."""
        ...


class SignatureMalwareScanner:
    """Escáner por firmas para el entorno local.

    Detecta la firma estándar EICAR y marcadores de ejecutables o
    scripts incrustados. Para producción debe sustituirse por un motor
    completo (p. ej. ClamAV) mediante esta misma interfaz; la decisión
    queda registrada en el expediente CHG-007.
    """

    _SIGNATURES = (
        b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
        b"MZ\x90\x00\x03",  # cabecera PE típica incrustada
        b"\x7fELF",
        b"<?php",
        b"<script",
    )

    def scan(self, data: bytes) -> bool:
        lowered = data.lower()
        return not any(
            signature.lower() in lowered for signature in self._SIGNATURES
        )


@dataclass(frozen=True)
class SanitizedPhoto:
    """Derivado seguro re-codificado sin metadatos."""

    content_type: str
    data: bytes
    extension: str


class PhotoProcessingError(Exception):
    """El contenido no pudo decodificarse como imagen válida."""


def strip_metadata(data: bytes, content_type: str) -> SanitizedPhoto:
    """Re-codifica la imagen eliminando EXIF y metadatos.

    La orientación EXIF se aplica antes de descartarla para no rotar
    la imagen. HEIC/HEIF se convierte a JPEG como derivado seguro.
    """
    if content_type in {"image/heic", "image/heif"} and not HEIF_SUPPORTED:
        raise PhotoProcessingError(
            "El entorno no puede decodificar HEIC/HEIF."
        )

    try:
        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            output = BytesIO()
            if content_type == "image/png":
                image.save(output, format="PNG")
                return SanitizedPhoto("image/png", output.getvalue(), "png")
            if content_type == "image/webp":
                image.save(output, format="WEBP", quality=90)
                return SanitizedPhoto("image/webp", output.getvalue(), "webp")
            # JPEG y HEIC/HEIF producen un derivado JPEG sin metadatos.
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(output, format="JPEG", quality=90)
            return SanitizedPhoto("image/jpeg", output.getvalue(), "jpg")
    except PhotoProcessingError:
        raise
    except Exception as error:
        raise PhotoProcessingError(
            "El archivo no es una imagen decodificable."
        ) from error
