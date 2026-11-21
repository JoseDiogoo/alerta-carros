"""AutoX."""

import html as htmllib
import re

from ..anuncio import carro
from ..texto import inteiro
from .http import descarregar


def autox(pagina):
    if pagina > 1:  # ponytail: /pagina/2 devolve a página 1; site pequeno, uma página chega
        return
    page = htmllib.unescape(descarregar("https://autox.pt/pesquisa-carros-usados/ordenar/ultimos-anuncios"))
    parts = re.split(r'href="(/detalhe-carros-usado/(\d+)/[^"]+)"', page)
    seen = set()
    for index in range(1, len(parts) - 2, 3):
        path, ad_id = parts[index], parts[index + 1]
        photo = re.search(r"https://images\.autox\.pt/[^\"'\s]+M\.webp", parts[index + 2][:4000])
        text = re.sub(r"\s*\|\s*(\|\s*)*", " | ", re.sub(r"<[^>]+>", " | ", parts[index + 2][:4000]))
        match = re.search(r"\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]*)\|\s*(\d{4})\s*-\s*(\d+)\s*kms\s*\|\s*(\d+)\s*€", text)
        if match and ad_id not in seen:
            seen.add(ad_id)
            brand, model, version, year, km, price = (g.strip() for g in match.groups())
            yield carro("autox", ad_id, f"{brand} {model} {version}", inteiro(price),
                        "https://autox.pt" + path, km=inteiro(km), ano=inteiro(year),
                        foto=photo and photo.group(), marca=brand, modelo_site=f"{model} {version}")
