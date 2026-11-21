"""Facebook Marketplace (categoria de veículos, sem login)."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..anuncio import carro
from ..texto import ano_do_texto, caixa, inteiro, km_do_texto, walk, words
from .http import HEADERS, descarregar


COMBUSTIVEL_FB = {"GASOLINE": "Gasolina", "DIESEL": "Gasóleo", "HYBRID": "Híbrido", "PLUGIN_HYBRID": "Híbrido",
                  "ELECTRIC": "Elétrico"}


def completar_facebook(anuncio):
    """A pesquisa do Facebook quase não traz km: a página do anúncio traz km, combustível, caixa e descrição."""
    page = descarregar(anuncio["url"])
    for block in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', page, re.S):
        if "vehicle_odometer_data" not in block:
            continue
        try:
            item = next((o for o in walk(json.loads(block)) if "vehicle_odometer_data" in o), None)
        except ValueError:
            continue
        if not item:
            continue
        odometro = item.get("vehicle_odometer_data") or {}
        km = inteiro(odometro.get("value"))
        if km and odometro.get("unit") == "MILES":
            km = round(km * 1.609)
        donos = inteiro(item.get("vehicle_number_of_owners"))
        anuncio.update(
            km=km if km and 1000 <= km < 1_000_000 else anuncio["km"],
            combustivel=COMBUSTIVEL_FB.get(item.get("vehicle_fuel_type")) or anuncio["combustivel"],
            caixa=caixa(item.get("vehicle_transmission_type")) or anuncio["caixa"],
            texto=(item.get("redacted_description") or {}).get("text") or anuncio["texto"],
            profissional={"PRIVATE_SELLER": False, "DEALER": True}.get(item.get("vehicle_seller_type"),
                                                                         anuncio["profissional"]),
            extras={**(anuncio.get("extras") or {}), **({"donos": donos} if donos else {})},
            marca=item.get("vehicle_make_display_name") or anuncio.get("marca"),
            modelo_site=item.get("vehicle_model_display_name") or anuncio.get("modelo_site"))
        return


def facebook(cidade, preco_min, preco_max, termo=None):
    """Categoria de veículos do Marketplace: abre sem login (a pesquisa por texto passou a pedir login).

    Traz também motas e acessórios: o que não é um modelo de carro é descartado depois. termo: filtra
    os títulos (a categoria ignora a pesquisa por texto).
    """
    query = urlencode({"minPrice": preco_min, "maxPrice": preco_max, "sortBy": "creation_time_descend",
                       "daysSinceListed": 1})
    url = f"https://www.facebook.com/marketplace/{quote(cidade)}/vehicles/?{query}"
    try:
        with urlopen(Request(url, headers=HEADERS), timeout=20) as response:
            if "/login" in response.geturl():  # sem isto o bloqueio passava por "0 anúncios"
                raise RuntimeError("www.facebook.com: pede login (bloqueado)")
            page = response.read().decode("utf-8", "replace")
    except HTTPError as error:
        raise RuntimeError(f"www.facebook.com: HTTP {error.code}") from None
    except (URLError, OSError):
        raise RuntimeError("www.facebook.com: falha de ligação") from None
    found = {}
    for block in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in walk(data):
            if "marketplace_listing_title" not in item or "id" not in item:
                continue
            if item.get("is_sold") or item.get("is_pending") or item.get("is_hidden"):
                continue
            title = item["marketplace_listing_title"]
            subtitles = " ".join(s.get("subtitle", "") for s in item.get("custom_sub_titles_with_rendering_flags") or [])
            found[item["id"]] = carro(
                "facebook", item["id"], title, inteiro((item.get("listing_price") or {}).get("amount")),
                f"https://www.facebook.com/marketplace/item/{item['id']}/",
                km=km_do_texto(f"{title} {subtitles}"), ano=ano_do_texto(title),
                local=((item.get("location") or {}).get("reverse_geocode") or {}).get("city") or "",
                foto=(((item.get("primary_listing_photo") or {}).get("image")) or {}).get("uri"))
    if termo:
        palavras = words(termo).split()
        found = {k: a for k, a in found.items() if all(w in words(a["titulo"]).split() for w in palavras)}
    yield from found.values()
