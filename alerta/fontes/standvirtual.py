"""Standvirtual."""

import json
import re

from .. import avaliacao as av
from ..anuncio import AVALIACAO_SITE, carro
from ..texto import caixa, combustivel, inteiro, walk
from .http import descarregar, next_data


def standvirtual(pagina, caminho="", preco_max=None):
    """caminho opcional "marca/modelo", ex.: "toyota/yaris". preco_max: só carros até esse preço."""
    teto = f"search%5Bfilter_float_price%3Ato%5D={preco_max}&" if preco_max else ""
    data = next_data(descarregar(f"https://www.standvirtual.com/carros/{caminho}?{teto}search%5Border%5D="
                                 f"created_at_first%3Adesc&page={pagina}"))
    for value in data["props"]["pageProps"]["urqlState"].values():
        result = json.loads(value["data"]) if isinstance(value.get("data"), str) else {}
        if "advertSearch" in result:
            break
    else:
        raise RuntimeError("resultados não encontrados (o site mudou?)")
    for edge in result["advertSearch"]["edges"]:
        node = edge["node"]
        params = {p["key"]: p["value"] for p in node.get("parameters") or []}
        amount = node["price"]["amount"]
        location = node.get("location") or {}
        yield carro("standvirtual", node["id"], node["title"],
                    inteiro(amount["units"]) if amount.get("currencyCode") == "EUR" else None,
                    node["url"], km=inteiro(params.get("mileage")),
                    ano=inteiro(params.get("first_registration_year")),
                    local=", ".join(filter(None, [(location.get("city") or {}).get("name"),
                                                  (location.get("region") or {}).get("name")])),
                    profissional=(node.get("seller") or {}).get("__typename") == "ProfessionalSeller",
                    importado=params.get("origin") == "imported" if "origin" in params else None,
                    avaliacao_site=AVALIACAO_SITE.get((node.get("priceEvaluation") or {}).get("indicator")),
                    texto=node.get("shortDescription") or "", destaque=bool(node.get("isPremiumTopAd")),
                    foto=(node.get("thumbnail") or {}).get("x2"),
                    combustivel=combustivel(params.get("fuel_type")), caixa=caixa(params.get("gearbox")),
                    cilindrada=inteiro(params.get("engine_capacity")), potencia=inteiro(params.get("engine_power")),
                    marca=params.get("make"), modelo_site=params.get("model"))


def completar_standvirtual(anuncio):
    """Página do anúncio: descrição completa, donos, garantia, 2.ª chave, origem."""
    data = next_data(descarregar(anuncio["url"]))
    advert = next(o for o in walk(data) if isinstance(o, dict) and "parametersDict" in o and "title" in o)
    anuncio["texto"] = re.sub(r"<[^>]+>", " ", advert.get("description") or "") or anuncio["texto"]
    anuncio["extras"] = av.extras_standvirtual(advert)
    if anuncio["extras"].get("importado") is not None:
        anuncio["importado"] = anuncio["extras"]["importado"]
