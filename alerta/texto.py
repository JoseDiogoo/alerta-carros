"""Texto dos anúncios: normalização, números, modelo, combustível e motor."""

import math
import re
import unicodedata


def words(value):
    normalized = unicodedata.normalize("NFKD", str(value).casefold())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        char for char in normalized if not unicodedata.combining(char))))


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def inteiro(value):
    try:
        number = float(re.sub(r"[\s ]", "", str(value)).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return int(number) if 0 < number < math.inf else None


def ano_do_texto(texto):
    match = re.search(r"\b(19[89]\d|20[0-3]\d)\b", texto or "")
    return int(match.group()) if match else None


def km_do_texto(texto):
    # "158 mil kms", "158.000 km", "158000km"
    match = re.search(r"\b(\d{1,3}(?:[.\s]\d{3})+|\d+)\s*(mil|k)?\s*(?:km|kms|quil[oó]metros)\b",
                      (texto or "").casefold())
    if not match:
        return None
    value = int(re.sub(r"\D", "", match.group(1))) * (1000 if match.group(2) else 1)
    return value if 1000 <= value < 1_000_000 else None


COMBUSTIVEIS = {"diesel": "Gasóleo", "gasoleo": "Gasóleo", "gasolina": "Gasolina", "petrol": "Gasolina",
                "gaz": "Gasolina", "gpl": "GPL", "lpg": "GPL", "hibrido": "Híbrido", "hybrid": "Híbrido",
                "electric": "Elétrico", "eletrico": "Elétrico", "electrico": "Elétrico"}


def combustivel(valor):
    return next((COMBUSTIVEIS[w] for w in words(valor or "").split() if w in COMBUSTIVEIS), None)


def caixa(valor):
    chave = words(valor or "")
    return "Automática" if "auto" in chave else "Manual" if "manual" in chave else None


def cilindrada_do_texto(texto):
    """"1.5 dCi" -> 1500 cc."""
    match = re.search(r"(?<![\d.,])([0-6])[.,](\d)(?![\d.,])", texto or "")
    return int(match.group(1)) * 1000 + int(match.group(2)) * 100 if match else None


def potencia_do_texto(texto):
    """"110 cv" / "150cv" / "90 hp" -> cavalos."""
    match = re.search(r"\b(\d{2,3})\s*(?:cv|hp|ps|ch)\b", (texto or "").casefold())
    return int(match.group(1)) if match and 40 <= int(match.group(1)) <= 800 else None


DIESEL = {"tdi", "dci", "cdi", "hdi", "crdi", "jtd", "jtdm", "tdci", "cdti", "dtec", "d4d", "multijet", "bluehdi",
          "ehdi", "tddi", "crd", "sdi", "dti", "idtec", "dtc", "tdv6", "sd4", "td4", "ed4"}


GASOLINA = {"tsi", "tfsi", "vvt", "vvti", "vti", "tce", "mpi", "fsi", "ecoboost", "puretech", "thp", "tjet", "gti",
            "skyactiv", "vtec", "16v", "turbo"}


def combustivel_do_titulo(titulo):
    """"1.6 TDI" / "320d" -> Gasóleo; "1.0 TSI" / "318i" -> Gasolina."""
    tokens = words(titulo).split()
    if explicito := combustivel(titulo):
        return explicito
    if any(t in DIESEL or re.fullmatch(r"\d{3}d", t) for t in tokens):
        return "Gasóleo"
    if any(t in GASOLINA or re.fullmatch(r"\d{3}i", t) for t in tokens):
        return "Gasolina"
    return None


def km_da_faixa(valor):
    """CustoJusto dá os km por faixa ("190 000 - 199 999"): usa o meio."""
    numeros = [int(re.sub(r"\D", "", n)) for n in re.findall(r"\d[\d\s .]*\d|\d", str(valor or ""))]
    return round(sum(numeros) / len(numeros)) if numeros and 0 < max(numeros) < 1_000_000 else None


def euros(valor):
    return f"{int(valor):,} €".replace(",", " ")
