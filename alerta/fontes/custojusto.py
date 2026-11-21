"""CustoJusto."""

from ..anuncio import carro
from ..texto import ano_do_texto, caixa, combustivel, inteiro, km_da_faixa, km_do_texto, walk
from .http import descarregar, next_data


def custojusto(pagina):
    url = "https://www.custojusto.pt/portugal/veiculos/carros-usados"
    data = next_data(descarregar(url + (f"?o={pagina}" if pagina > 1 else "")))
    items = next((o["listItems"] for o in walk(data) if isinstance(o.get("listItems"), list)), None)
    if items is None:
        raise RuntimeError("resultados não encontrados (o site mudou?)")
    for ad in items:
        names = ad.get("locationNames") or {}
        text = f"{ad.get('title', '')} {ad.get('body', '')}"
        yield carro("custojusto", ad["listID"], ad.get("title"), inteiro(ad.get("price")),
                    "https://www.custojusto.pt" + ad["url"], km=km_do_texto(text),
                    ano=inteiro((ad.get("params") or {}).get("regdate")) or ano_do_texto(text),
                    local=", ".join(filter(None, [names.get("county"), names.get("district")])),
                    profissional=ad.get("companyAd"), texto=ad.get("body") or "",
                    foto=ad.get("imageFullURL"),
                    combustivel=combustivel((ad.get("params") or {}).get("fuel")),
                    caixa=caixa((ad.get("params") or {}).get("gearbox")))


def completar_custojusto(anuncio):
    """A lista do CustoJusto quase nunca traz km: a página do anúncio traz a faixa."""
    data = next_data(descarregar(anuncio["url"]))
    params = next(o for o in walk(data) if "mileage" in o)  # o 1.º é o do anúncio (os outros são sugestões)
    anuncio["km"] = km_da_faixa(params["mileage"]) or anuncio["km"]
    anuncio["ano"] = anuncio["ano"] or inteiro(params.get("regdate"))
