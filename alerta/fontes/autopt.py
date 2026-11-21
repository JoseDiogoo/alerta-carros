"""Auto.pt."""

import html as htmllib
import json
import re

from ..anuncio import carro
from ..texto import caixa, combustivel, inteiro
from .http import descarregar


def autopt(pagina):
    page = descarregar(f"https://www.auto.pt/carros-usados?sort=data-anuncio&page={pagina}")
    page = re.sub(r"<(script|style|svg)\b[^>]*>.*?</\1>", "", page, flags=re.S)
    parts = re.split(r'<a[^>]+href="(/carros-usados/[^"?#]+-id-(\w+))"', page)
    found = {}
    for index in range(1, len(parts) - 2, 3):
        path, ad_id = parts[index], parts[index + 1]
        photo = re.search(r'src="(https://images\.auto\.pt/[^"]+)"', parts[index + 2])
        text = htmllib.unescape(re.sub(r"<[^>]+>", " ", parts[index + 2].split(">", 1)[-1]))
        price = re.search(r"(\d[\d\s ]*)\s*€", text)
        if price and ad_id not in found:
            # título provisório a partir do endereço; o detalhe traz o título real
            title = path.rsplit("/", 1)[-1].rsplit("-id-", 1)[0].replace("-", " ")
            found[ad_id] = carro("autopt", ad_id, title, inteiro(price.group(1)),
                                 "https://www.auto.pt" + path, foto=photo and photo.group(1))
    yield from found.values()


def completar_autopt(anuncio):
    """A lista do auto.pt não traz km/ano: lê o anúncio só para candidatos."""
    page = descarregar(anuncio["url"])
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Vehicle":
            seller = (data.get("offers") or {}).get("seller") or {}
            address = seller.get("address") or {}
            anuncio.update(titulo=data.get("name") or anuncio["titulo"],
                           km=inteiro((data.get("mileageFromOdometer") or {}).get("value")),
                           ano=inteiro(str(data.get("dateVehicleFirstRegistered", ""))[:4]),
                           profissional=seller.get("@type") == "AutoDealer",
                           local=address.get("addressLocality") or "",
                           texto=data.get("description") or "",
                           combustivel=combustivel(data.get("fuelType")) or anuncio["combustivel"],
                           caixa=caixa(data.get("vehicleTransmission") or data.get("name")) or anuncio["caixa"],
                           carrocaria=data.get("bodyType") or anuncio.get("carrocaria"))
            return
