"""CHG-107 — Calidad mínima del texto aportado por la comunidad."""

import pytest

from app.text_quality import (
    MIN_DISTINCT_WORDS,
    TextQualityError,
    distinct_words,
    has_excessive_repetition,
    has_overlong_word,
    validate_community_text,
)


DESCRIPCION_REAL = (
    "La vi caminando cerca del parque principal sobre la calle "
    "veinticuatro, vestía chaqueta azul y llevaba una mochila."
)


def test_una_descripcion_real_pasa():
    assert validate_community_text(DESCRIPCION_REAL, "campo")


def test_el_spam_de_caracteres_repetidos_se_rechaza():
    # 30 caracteres: cumplía el mínimo de longitud anterior.
    basura = "a" * 30
    assert has_excessive_repetition(basura)
    with pytest.raises(TextQualityError):
        validate_community_text(basura, "campo")


def test_tres_repeticiones_siguen_siendo_lenguaje():
    # "holaaa" o "nooo" son escritura real, no spam.
    assert not has_excessive_repetition("holaaa que tal, noooo se")


def test_una_sola_palabra_repetida_no_describe_nada():
    with pytest.raises(TextQualityError):
        validate_community_text("casa casa casa casa casa casa", "campo")


def test_las_palabras_distintas_ignoran_tildes_y_mayusculas():
    assert distinct_words("Casa casa CASA cásá") == 1
    assert distinct_words("perro gato loro pez ave") == 5


def test_una_cadena_pegada_larga_se_rechaza():
    pegada = "x" * 45
    assert has_overlong_word(pegada)


def test_exige_un_minimo_de_palabras_distintas():
    pocas = "vi a la persona alli"
    if distinct_words(pocas) < MIN_DISTINCT_WORDS:
        with pytest.raises(TextQualityError):
            validate_community_text(pocas, "campo")


# CHG-146: el umbral de palabras distintas es parametrizable. La
# solicitud de ayuda lo baja a 3 (descripciones de emergencia breves).
def test_umbral_de_palabras_parametrizable_para_solicitud_de_ayuda():
    # «Necesito ayuda urgente aquí» (4 palabras) se rechazaba con 5 y
    # debe pasar con 3.
    breve = "Necesito ayuda urgente aqui"
    assert distinct_words(breve) == 4
    with pytest.raises(TextQualityError):
        validate_community_text(breve, "description")
    assert validate_community_text(breve, "description", min_distinct_words=3)


def test_umbral_menor_sigue_descartando_basura():
    # Con 3 palabras distintas, 1-2 palabras y el spam de caracteres
    # siguen cayendo.
    with pytest.raises(TextQualityError):
        validate_community_text("ayuda ayuda ayuda", "description", min_distinct_words=3)
    with pytest.raises(TextQualityError):
        validate_community_text("a" * 30, "description", min_distinct_words=3)
