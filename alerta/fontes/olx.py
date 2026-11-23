"""OLX."""

import json
import re
from urllib.parse import quote

from ..anuncio import carro
from ..texto import caixa, combustivel, data_utc, inteiro, walk
from .http import descarregar


def olx(pagina, termo=None, preco_max=None):
    base = "https://www.olx.pt/carros-motos-e-barcos/carros/"
    if termo:
        base += f"q-{quote(termo.replace(' ', '-'))}/"
    teto = f"search%5Bfilter_float_price%3Ato%5D={preco_max}&" if preco_max else ""
    page = descarregar(f"{base}?{teto}search%5Border%5D=created_at:desc&page={pagina}")
    start = page.find("__PRERENDERED_STATE__")
    if start < 0:
        raise RuntimeError("página sem dados de anúncios (o site mudou?)")
    state = json.loads(json.JSONDecoder().raw_decode(page, page.find('"', start))[0])
    # a marca é a categoria do anúncio (ex.: 607 = Porsche); a lista de categorias vem na própria página
    marcas = {c["id"]: c.get("normalizedName") for c in walk(state.get("categories") or {})
              if isinstance(c, dict) and "id" in c and "normalizedName" in c}
    for ad in state["listing"]["listing"]["ads"]:
        price = (ad.get("price") or {}).get("regularPrice") or {}
        params = {p.get("key"): p.get("normalizedValue") for p in ad.get("params") or []}
        yield carro("olx", ad["id"], ad["title"],
                    inteiro(price.get("value")) if price.get("currencyCode") == "EUR" else None,
                    ad["url"], km=inteiro(params.get("quilometros")), ano=inteiro(params.get("year")),
                    local=(ad.get("location") or {}).get("pathName") or "",
                    profissional=ad.get("isBusiness"),
                    importado=params.get("origin") == "imported" if params.get("origin") else None,
                    texto=ad.get("description") or "", destaque=bool(ad.get("isPromoted")),
                    foto=re.sub(r";s=\d+x\d+", ";s=640x480", (ad.get("photos") or [""])[0]) or None,
                    combustivel=combustivel(params.get("combustivel")), caixa=caixa(params.get("gearbox")),
                    cilindrada=inteiro(params.get("engine_capacity")), potencia=inteiro(params.get("engine_power")),
                    carrocaria=params.get("body_type"),
                    marca=marcas.get((ad.get("category") or {}).get("id")), modelo_site=params.get("modelo"),
                    publicado_em=data_utc(ad.get("createdTime")))
