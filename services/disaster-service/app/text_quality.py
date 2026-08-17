"""CHG-107 — Calidad mínima del texto aportado por la comunidad.

La descripción de una novedad exigía 30 caracteres, pero treinta letras
iguales los cumplen: se podía contaminar el expediente de una persona
desaparecida con texto basura. Estas reglas no juzgan el contenido —eso
es del equipo de revisión—, solo descartan lo que evidentemente no es
una descripción escrita por alguien.
"""

import re
import unicodedata

# Cuatro letras iguales seguidas ("aaaa") no aparecen en español; tres
# sí, en interjecciones y onomatopeyas, así que el corte va en cuatro.
MAX_REPEATED_CHARACTERS = 4

# Palabras reales distintas que debe traer una descripción para que
# aporte algo. Una frase corta y útil las supera de sobra.
MIN_DISTINCT_WORDS = 5

# Longitud a partir de la cual una secuencia sin espacios deja de ser
# una palabra y pasa a ser una cadena pegada.
MAX_WORD_LENGTH = 40

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


class TextQualityError(ValueError):
    """Texto rechazado; el mensaje es seguro para el cliente."""


def _strip_accents(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )


def has_excessive_repetition(value: str) -> bool:
    """¿Hay un carácter repetido más allá de lo que escribe una persona?"""
    limite = MAX_REPEATED_CHARACTERS
    repetidos = 1
    anterior = ""
    for char in value:
        if char == anterior and not char.isspace():
            repetidos += 1
            if repetidos > limite:
                return True
        else:
            repetidos = 1
            anterior = char
    return False


def distinct_words(value: str) -> int:
    """Palabras distintas, sin tildes ni mayúsculas: 'Casa casa' es una."""
    encontradas = {
        _strip_accents(match.group(0)).casefold()
        for match in _WORD.finditer(value)
    }
    return len(encontradas)


def has_overlong_word(value: str) -> bool:
    return any(
        len(fragmento) > MAX_WORD_LENGTH for fragmento in value.split()
    )


def validate_community_text(
    value: str, field: str, min_distinct_words: int = MIN_DISTINCT_WORDS
) -> str:
    """Devuelve el texto recortado o explica por qué no sirve.

    CHG-146: `min_distinct_words` es parametrizable. El reporte de
    persona conserva 5; la solicitud de ayuda (CHG-125), que es terse
    por naturaleza («Necesito ayuda urgente aquí»), usa un mínimo menor
    sin dejar de descartar basura (repetición, cadenas pegadas, 1-2
    palabras).
    """
    texto = value.strip()

    if has_excessive_repetition(texto):
        raise TextQualityError(
            f"{field} repite un mismo carácter demasiadas veces; "
            "describe lo que observaste."
        )

    if has_overlong_word(texto):
        raise TextQualityError(
            f"{field} contiene una secuencia sin espacios demasiado "
            "larga; describe lo que observaste."
        )

    if distinct_words(texto) < min_distinct_words:
        raise TextQualityError(
            f"{field} necesita al menos {min_distinct_words} palabras "
            "distintas que describan lo que observaste."
        )

    return texto
