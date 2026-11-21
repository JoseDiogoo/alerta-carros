"""Alertas de carros bons e baratos no Telegram, lidos diretamente dos sites (sem serviços pagos)."""

import argparse
import html as htmllib
import json
import math
import os
import re
import sqlite3
import statistics
import threading
import time
import tomllib
import unicodedata

import avaliacao as av
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "text/html", "Accept-Language": "pt-PT,pt", "Sec-Fetch-Mode": "navigate",
}
NOMES = {"standvirtual": "Standvirtual", "olx": "OLX", "custojusto": "CustoJusto",
         "autopt": "Auto.pt", "autox": "AutoX", "facebook": "Facebook Marketplace"}
AVALIACAO_SITE = {"BELOW": "abaixo do mercado", "IN": "dentro do mercado", "ABOVE": "acima do mercado"}


# ---------- utilitários ----------

def api(service, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=12) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"{service}: HTTP {error.code}") from None
    except (URLError, OSError, ValueError):
        raise RuntimeError(f"{service}: ligação ou resposta inválida") from None


def descarregar(url):
    site = url.split("/")[2]
    try:
        with urlopen(Request(url, headers=HEADERS), timeout=20) as response:
            return response.read().decode("utf-8", "replace")
    except HTTPError as error:
        raise RuntimeError(f"{site}: HTTP {error.code}") from None
    except (URLError, OSError):
        raise RuntimeError(f"{site}: falha de ligação") from None


def secret(name):
    value = os.environ.get(name, "").strip()
    segredos = ROOT / ".streamlit" / "secrets.toml"
    if not value and segredos.is_file():
        with segredos.open("rb") as file:
            value = str(tomllib.load(file).get(name, "")).strip()
    if not value:
        raise ValueError(f"Define {name} em .streamlit/secrets.toml ou no PowerShell.")
    return value


def telegram(method, **data):
    token = secret("TELEGRAM_BOT_TOKEN")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("O token do Telegram tem um formato inválido.")
    response = api("Telegram", f"https://api.telegram.org/bot{token}/{method}", data=data)
    if not response.get("ok"):
        raise RuntimeError("O Telegram rejeitou o pedido.")
    return response["result"]


ULTIMO_ENVIO = [0.0]
ENVIO = threading.Lock()


def send(message, foto=None):
    with ENVIO:  # as fontes correm em paralelo: o ritmo do Telegram é partilhado
        # Telegram: grupos aceitam ~20 mensagens/minuto; acima disso responde HTTP 429
        espera = ULTIMO_ENVIO[0] + 3.2 - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        ULTIMO_ENVIO[0] = time.monotonic()
        _enviar(message, foto)


def _enviar(message, foto):
    chat = secret("TELEGRAM_CHAT_ID")
    if foto and len(message) <= 1024:
        try:
            telegram("sendPhoto", chat_id=chat, photo=foto, caption=message)
            return
        except RuntimeError:
            pass  # foto recusada pelo Telegram: segue em texto
    telegram("sendMessage", chat_id=chat, text=message, link_preview_options={"is_disabled": True})


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


VERSOES_DESPORTIVAS = {"gti", "gtd", "gte", "r", "rs", "amg", "cupra", "st", "vrs", "type", "abarth", "nismo",
                       "m3", "m4", "m5", "jcw", "srt", "svr", "gsi", "opc", "ts"}
MODELOS_ALIAS = {"corola": "corolla", "yaries": "yaris", "auriss": "auris", "aigo": "aygo", "megan": "megane",
                 "meganne": "megane", "clio4": "clio", "pugeot": "peugeot", "peujeot": "peugeot", "octava": "octavia",
                 "ibisa": "ibiza", "fiesa": "fiesta", "corssa": "corsa"}
MARCAS_ALIAS_ERROS = {"pugeot": "peugeot", "peujeot": "peugeot", "renaul": "renault", "reanult": "renault",
                      "toyta": "toyota", "volksvagen": "volkswagen", "mercedez": "mercedes-benz"}
IGNORAR_NO_INICIO = {"vendo", "vende", "se", "venda", "novo", "usado", "carro", "urgente", "oportunidade"}
MARCAS_ALIAS = {"vw": "volkswagen", "mercedes": "mercedes-benz", "merc": "mercedes-benz",
                "alfa": "alfa-romeo", "land": "land-rover", "range": "land-rover"}


def modelo_de(titulo):
    """"2015 VW Golf 1.6 TDI" -> "volkswagen golf". Sem lista fixa: marca + 1.ª palavra do modelo."""
    # ponytail: heurística pelo título; usar make/model estruturados se aparecerem modelos trocados
    tokens = [t for t in words(titulo).split() if not re.fullmatch(r"(19|20)\d\d", t)]
    while tokens and tokens[0] in IGNORAR_NO_INICIO:
        tokens.pop(0)
    if len(tokens) < 2:
        return None
    marca = MARCAS_ALIAS_ERROS.get(tokens[0], tokens[0])
    marca, resto = MARCAS_ALIAS.get(marca, marca), tokens[1:]
    while resto and resto[0] in {"benz", "romeo", "rover"}:
        resto = resto[1:]
    if not resto:
        return None
    resto = [MODELOS_ALIAS.get(t, t) for t in resto]  # erros de escrita frequentes
    serie_bmw = re.fullmatch(r"([1-8])\d\d[a-z]*", resto[0]) if marca == "bmw" else None
    if serie_bmw:  # "BMW 320d" = "BMW Série 3"
        modelo = f"bmw serie {serie_bmw.group(1)}"
    elif resto[0] in {"classe", "class"} and len(resto) > 1:  # "Classe A 180" = "A 180"
        modelo = f"{marca} {resto[1]}"
    elif resto[0] in {"model", "serie", "series", "grand", "range"} and len(resto) > 1:
        modelo = f"{marca} {'serie' if resto[0] == 'series' else resto[0]} {resto[1]}"
    else:
        modelo = f"{marca} {resto[0]}"
    # "R-Line", "ST-Line", "AMG Line" são acabamentos, não versões desportivas
    desportiva = next((t for i, t in enumerate(resto[1:], 1) if t in VERSOES_DESPORTIVAS
                       and resto[i + 1:i + 2] != ["line"]), None)
    return f"{modelo} {desportiva}" if desportiva else modelo


def next_data(page):
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not match:
        raise RuntimeError("página sem dados de anúncios (o site mudou?)")
    return json.loads(match.group(1))


def carro(origem, id, titulo, preco, url, **extra):
    return {"origem": origem, "id": str(id), "titulo": (titulo or "").strip(), "preco": preco,
            "url": url, "km": None, "ano": None, "local": "", "profissional": None,
            "importado": None, "avaliacao_site": None, "texto": "", "destaque": False, "foto": None,
            "combustivel": None, "caixa": None, "cilindrada": None, "potencia": None, "carrocaria": None, **extra}


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


def completar_motor(anuncio):
    """Cilindrada, potência e combustível pelo título quando o site não os dá em campos próprios."""
    anuncio["combustivel"] = anuncio.get("combustivel") or combustivel_do_titulo(anuncio["titulo"])
    anuncio["cilindrada"] = anuncio.get("cilindrada") or cilindrada_do_texto(anuncio["titulo"])
    anuncio["potencia"] = anuncio.get("potencia") or potencia_do_texto(
        f"{anuncio['titulo']} {(anuncio.get('texto') or '')[:300]}")


# ---------- fontes ----------

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
                    cilindrada=inteiro(params.get("engine_capacity")), potencia=inteiro(params.get("engine_power")))


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
                    carrocaria=params.get("body_type"))


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


def detalhe(anuncio):
    """Lê a página do anúncio (descrição completa e características) para avaliar a fundo."""
    try:
        if anuncio["origem"] == "standvirtual":
            data = next_data(descarregar(anuncio["url"]))
            advert = next(o for o in walk(data) if isinstance(o, dict) and "parametersDict" in o and "title" in o)
            anuncio["texto"] = re.sub(r"<[^>]+>", " ", advert.get("description") or "") or anuncio["texto"]
            anuncio["extras"] = av.extras_standvirtual(advert)
            if anuncio["extras"].get("importado") is not None:
                anuncio["importado"] = anuncio["extras"]["importado"]
        elif anuncio["origem"] == "autopt":
            completar_autopt(anuncio)
        elif anuncio["origem"] == "custojusto" and not anuncio["km"]:
            completar_custojusto(anuncio)
        elif anuncio["origem"] == "facebook":
            completar_facebook(anuncio)
    except (RuntimeError, StopIteration, KeyError, ValueError):
        pass  # sem detalhe, avalia com o que a lista trouxe
    # o que os campos não trouxeram, procura-se na descrição ("158 mil km", "ano 2012")
    anuncio["km"] = anuncio["km"] or km_do_texto(anuncio["texto"])
    anuncio["ano"] = anuncio["ano"] or ano_do_texto(anuncio["texto"])
    anuncio["sinais"] = av.sinais_do_texto(anuncio["titulo"], anuncio["texto"])
    anuncio.setdefault("extras", {})
    return anuncio


def candidato(db, anuncio, config):
    """Passa no mercado/margem, antes de olhar à descrição? (decide se vale ler a página)."""
    if anuncio["origem"] == "autopt" and anuncio["km"] is None:
        return config["preco_min"] <= anuncio["preco"] <= config["preco_max"]
    neutro = {"graves": [], "leves": [], "bons": [], "tem_descricao": True}
    return avaliar(db, {**anuncio, "sinais": neutro}, {**config, "pontuacao_minima": 0}) is not None


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


def km_da_faixa(valor):
    """CustoJusto dá os km por faixa ("190 000 - 199 999"): usa o meio."""
    numeros = [int(re.sub(r"\D", "", n)) for n in re.findall(r"\d[\d\s .]*\d|\d", str(valor or ""))]
    return round(sum(numeros) / len(numeros)) if numeros and 0 < max(numeros) < 1_000_000 else None


def completar_custojusto(anuncio):
    """A lista do CustoJusto quase nunca traz km: a página do anúncio traz a faixa."""
    data = next_data(descarregar(anuncio["url"]))
    params = next(o for o in walk(data) if "mileage" in o)  # o 1.º é o do anúncio (os outros são sugestões)
    anuncio["km"] = km_da_faixa(params["mileage"]) or anuncio["km"]
    anuncio["ano"] = anuncio["ano"] or inteiro(params.get("regdate"))


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
            extras={**(anuncio.get("extras") or {}), **({"donos": donos} if donos else {})})
        return


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
                        foto=photo and photo.group())


def facebook(termo, cidade, preco_min, preco_max):
    query = urlencode({"query": termo, "minPrice": preco_min, "maxPrice": preco_max,
                       "daysSinceListed": 1, "exact": "false"})
    page = descarregar(f"https://www.facebook.com/marketplace/{quote(cidade)}/search/?{query}")
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
    yield from found.values()


PAGINADAS = {"standvirtual": standvirtual, "olx": olx, "custojusto": custojusto,
             "autopt": autopt, "autox": autox}


# ---------- avaliação ----------

def interessa(anuncio, config):
    """Filtros do utilizador (config.toml ou painel). Lista de modelos vazia = todos os carros."""
    if not anuncio.get("modelo") or excluido(anuncio, config):
        return False
    modelos = [words(m) for m in config["modelos"]]
    return not modelos or anuncio["modelo"].replace("-", " ") in [m.replace("-", " ") for m in modelos] \
        or any(f" {m} " in f" {words(anuncio['titulo'])} " for m in modelos)


def excluido(anuncio, config):
    title = f" {words(anuncio['titulo'])} "
    text = f" {words(anuncio['titulo'] + ' ' + anuncio['texto'])} "
    return (any(f" {words(term)} " in title for term in config["excluir"])
            or any(f" {words(term)} " in text for term in config["excluir_texto"]))


def dentro_dos_limites(anuncio, config):
    price, km, year = anuncio["preco"], anuncio["km"], anuncio["ano"]
    if not config["preco_min"] <= price <= config["preco_max"]:
        return False
    if year and year < config["ano_min"]:
        return False
    if km and km > config["km_max"]:
        return False
    if km and year and km / max(1, datetime.now().year - year) > config["km_ano_max"]:
        return False
    return not (config["apenas_nacionais"] and anuncio["importado"])


def mercado(db, anuncio, minimo=1):
    info = comparar(db, anuncio, minimo)
    return info["mediana"], info["n"]


# Desvalorização típica quando o modelo não tem anúncios suficientes para a aprender:
# 7 % por ano de idade e 2,5 % por cada 10 000 km.
DEPRECIACAO_PADRAO = (0.07, -0.025)


def _ols(pontos):
    """Mínimos quadrados de y = a + b1*x1 + b2*x2. Devolve (b1, b2) ou None."""
    n = len(pontos)
    m1, m2, my = (sum(p[i] for p in pontos) / n for i in range(3))
    s11 = sum((p[0] - m1) ** 2 for p in pontos)
    s22 = sum((p[1] - m2) ** 2 for p in pontos)
    s12 = sum((p[0] - m1) * (p[1] - m2) for p in pontos)
    s1y = sum((p[0] - m1) * (p[2] - my) for p in pontos)
    s2y = sum((p[1] - m2) * (p[2] - my) for p in pontos)
    det = s11 * s22 - s12 ** 2
    if det <= 1e-9:
        return None
    return (s1y * s22 - s2y * s12) / det, (s2y * s11 - s1y * s12) / det


def depreciacao(db, anuncio):
    """Quanto este modelo perde por ano e por 10 000 km, aprendido dos anúncios (log-preço ~ ano + km)."""
    sql = ("SELECT preco, ano, km FROM anuncios WHERE modelo = ? AND ano BETWEEN ? AND ? AND km > 0 "
           "AND preco >= 300")
    args = [anuncio["modelo"], anuncio["ano"] - 4, anuncio["ano"] + 4]
    if anuncio.get("combustivel"):
        sql += " AND (combustivel = ? OR combustivel IS NULL)"
        args.append(anuncio["combustivel"])
    pontos = [(ano, km / 10000, math.log(preco)) for preco, ano, km in db.execute(sql, args)]
    coef = _ols(pontos) if len(pontos) >= 15 else None
    if not coef:
        return DEPRECIACAO_PADRAO
    # limites sensatos: uma amostra pequena ou estranha não pode dar valores absurdos
    return min(0.20, max(0.02, coef[0])), min(-0.005, max(-0.08, coef[1]))


def _parecido(valor, alvo, tolerancia, aceita_desconhecido):
    if alvo is None:
        return True
    if valor is None:
        return aceita_desconhecido
    return abs(valor - alvo) <= tolerancia


def comparar(db, anuncio, minimo=1):
    """Valor de mercado rigoroso.

    1. Só carros do mesmo modelo, combustível, ano ±2 e km parecidos.
    2. Procura primeiro os de motor (cilindrada, potência) e caixa iguais; só alarga se forem poucos.
    3. Ajusta o preço de cada um à diferença de ano e de km (desvalorização aprendida por modelo).
    4. Tira preços absurdos (peças, erros) e usa a mediana.
    """
    vazio = {"mediana": None, "prudente": None, "n": 0, "km_mediano": None, "nivel": None, "dispersao": None}
    if not (anuncio.get("modelo") and anuncio["ano"]):
        return vazio
    sql = ("SELECT preco, km, ano, caixa, cilindrada, potencia, origem, id FROM anuncios "
           "WHERE modelo = ? AND ano BETWEEN ? AND ? AND NOT (origem = ? AND id = ?) AND preco >= 300")
    # ano ±2 e km ±45%: cada preço é depois ajustado à diferença de ano e km, por isso a janela pode ser larga
    args = [anuncio["modelo"], anuncio["ano"] - 2, anuncio["ano"] + 2, anuncio["origem"], anuncio["id"]]
    if anuncio.get("combustivel"):  # um híbrido vale bem mais do que o mesmo modelo a gasolina
        sql += " AND (combustivel = ? OR combustivel IS NULL)"
        args.append(anuncio["combustivel"])
    if anuncio["km"]:  # só compara com km parecidos: sem amostra não há estimativa (melhor que um valor errado)
        margem_km = max(35000, anuncio["km"] * 0.45)
        sql += " AND km BETWEEN ? AND ?"
        args += [anuncio["km"] - margem_km, anuncio["km"] + margem_km]
    unicos = {}
    for row in db.execute(sql, args):  # o mesmo carro publicado em vários sites conta uma só vez
        unicos.setdefault((row[0], row[2], row[1] or (row[6], row[7])), row)
    rows = list(unicos.values())

    caixa_alvo, cc, cv = anuncio.get("caixa"), anuncio.get("cilindrada"), anuncio.get("potencia")

    def iguais(r, desconhecido):
        return ((caixa_alvo is None or r[3] == caixa_alvo or (desconhecido and r[3] is None))
                and _parecido(r[4], cc, 45, desconhecido)
                and _parecido(r[5], cv, (cv or 0) * 0.15, desconhecido))

    niveis = [("mesmo motor e caixa", lambda r: iguais(r, False)),
              ("motor e caixa compatíveis", lambda r: iguais(r, True)),
              ("mesmo modelo e combustível", lambda r: True)]
    if not (caixa_alvo or cc or cv):
        niveis = niveis[2:]
    for nivel, aceita in niveis:
        escolhidos = [r for r in rows if aceita(r)]
        if len(escolhidos) >= max(1, minimo):
            break
    else:
        return {**vazio, "n": len(rows)}

    b_ano, b_km = depreciacao(db, anuncio)
    ajustados = []
    for preco, km, ano, *_ in escolhidos:
        delta = b_ano * (anuncio["ano"] - ano)
        if anuncio["km"] and km:
            delta += b_km * (anuncio["km"] - km) / 10000
        ajustados.append(preco * math.exp(delta))
    centro = statistics.median(ajustados)
    limpos = [p for p in ajustados if 0.6 * centro <= p <= 1.6 * centro]
    if len(limpos) >= max(1, minimo):
        ajustados = limpos
    mediana = statistics.median(ajustados)
    quartis = statistics.quantiles(ajustados, n=4) if len(ajustados) >= 4 else None
    p30 = statistics.quantiles(ajustados, n=10)[2] if len(ajustados) >= 4 else min(ajustados)
    kms = [r[1] for r in escolhidos if r[1]]
    return {"mediana": mediana, "prudente": min(mediana, p30), "n": len(ajustados), "nivel": nivel,
            "km_mediano": statistics.median(kms) if kms else None,
            "dispersao": (quartis[2] - quartis[0]) / mediana if quartis else None}


def analisar(db, anuncio, config):
    """Valor de mercado, venda provável, margem e pontuação (sem aplicar os mínimos do utilizador)."""
    info = comparar(db, anuncio, config["min_comparaveis"])
    sinais = anuncio.get("sinais") or av.sinais_do_texto(anuncio["titulo"], anuncio["texto"])
    if info["mediana"] is None:
        return {**info, "venda": None, "margem": None, "desconto": None, "ratio": None, "suspeito": False,
                "fiavel": False, "prudente_usado": False, "irreal": False, "avaria": av.avaria(sinais),
                "pontuacao": None, "fortes": [], "alertas": [], "sinais": sinais}
    # Comparação fraca (sem km, só o modelo, preços muito variáveis): usa um valor prudente (percentil 30)
    # em vez da mediana, para a margem nunca ser inflacionada por falta de dados.
    fraca = (not anuncio["km"] or info["nivel"] == "mesmo modelo e combustível"
             or (info["dispersao"] or 0) > 0.35)
    mediana = info["prudente"] if fraca else info["mediana"]
    venda = mediana * config["fator_venda"]  # os anúncios são preços pedidos: vende-se um pouco abaixo
    # avarias: só etiqueta, sem custo (a gravidade é desconhecida); a margem é a do carro como se estivesse bom
    analise = {**info, "mediana": round(mediana), "prudente_usado": fraca, "venda": round(venda),
               "avaria": av.avaria(sinais),
               "margem": round(venda - anuncio["preco"] - config["custos_revenda"]),
               "desconto": round((1 - anuncio["preco"] / mediana) * 100), "ratio": anuncio["preco"] / mediana,
               "fiavel": (info["dispersao"] or 0) <= 0.6,
               # tão abaixo do mercado que é quase sempre burla, erro no preço ou valor de uma prestação
               "irreal": anuncio["preco"] < mediana * config["limite_irreal"],
               "suspeito": anuncio["preco"] < mediana * config["limite_burla"], "sinais": sinais}
    analise["pontuacao"], analise["fortes"], analise["alertas"] = av.pontuar(
        anuncio, analise, sinais, anuncio.get("extras"))
    return analise


def avaliar(db, anuncio, config):
    """Análise, se o carro for dos melhores negócios (vai para o Telegram); senão None."""
    if not (interessa(anuncio, config) and dentro_dos_limites(anuncio, config)):
        return None
    analise = analisar(db, anuncio, config)
    return analise if melhor(anuncio, analise, config) else None


def de_foco(anuncio, config):
    """Marca em foco (marcas_foco, ex.: Toyota): critério do Telegram mais solto."""
    return (anuncio.get("modelo") or "").split(" ")[0] in [words(m) for m in config["marcas_foco"]]


def melhor(anuncio, analise, config):
    """Critério do Telegram: muito abaixo de um valor de mercado fiável e boa pontuação. Avariados contam."""
    if analise["mediana"] is None or not analise["fiavel"] or analise["irreal"]:  # disperso ou preço irreal
        return False
    if analise["avaria"]["inviaveis"]:
        return False  # peças, problemas legais ou estruturais: não é negócio
    desconto = config["desconto_foco"] if de_foco(anuncio, config) else config["desconto_telegram"]
    return (analise["ratio"] <= 1 - desconto and analise["n"] >= config["comparaveis_telegram"]
            and analise["pontuacao"] >= config["pontuacao_minima"])


def duplicado(db, anuncio):
    if not (anuncio["km"] and anuncio["ano"]):
        return False
    return db.execute(
        "SELECT 1 FROM anuncios WHERE alertado = 1 AND modelo = ? AND ano = ? AND km = ? "
        "AND ABS(preco - ?) <= 250 AND origem != ?",
        (anuncio["modelo"], anuncio["ano"], anuncio["km"], anuncio["preco"], anuncio["origem"])).fetchone() is not None


def euros(valor):
    return f"{int(valor):,} €".replace(",", " ")


ENVIADOS_TODOS = [0]


def mensagem_simples(anuncio):
    """Alerta de qualquer anúncio novo (opção alertar_todos), sem análise de negócio."""
    facts = [euros(anuncio["preco"]), str(anuncio["ano"] or ""),
             f"{anuncio['km']:,} km".replace(",", " ") if anuncio["km"] else "",
             anuncio["combustivel"] or "", (anuncio["local"] or "").split(",")[0]]
    return "\n".join([f"Novo anúncio ({NOMES[anuncio['origem']]})", anuncio["titulo"][:150],
                      " · ".join(x for x in facts if x), anuncio["url"]])


def mensagem(anuncio, analise, old_price):
    facts = [euros(anuncio["preco"])]
    if anuncio["ano"]:
        facts.append(str(anuncio["ano"]))
    if anuncio["km"]:
        facts.append(f"{anuncio['km']:,} km".replace(",", " "))
    if anuncio.get("cilindrada"):
        facts.append(f"{anuncio['cilindrada'] / 1000:.1f}")
    if anuncio.get("potencia"):
        facts.append(f"{anuncio['potencia']} cv")
    for extra in (anuncio["combustivel"], anuncio["caixa"]):
        if extra:
            facts.append(extra)
    if anuncio["profissional"] is not None:
        facts.append("profissional" if anuncio["profissional"] else "particular")
    if anuncio["importado"]:
        facts.append("importado")
    avaria = analise.get("avaria") or {}
    header = f"{anuncio['modelo'].title()} · {NOMES[anuncio['origem']]}"
    if anuncio["local"]:
        header += f" · {anuncio['local'][:60]}"
    lines = [header, anuncio["titulo"][:150], " · ".join(facts),
             f"Pontuação {analise['pontuacao']}/100",
             f"Margem estimada: +{euros(analise['margem'])} (venda provável ~{euros(analise['venda'])})",
             f"Mercado ~{euros(analise['mediana'])}{' (valor prudente)' if analise.get('prudente_usado') else ''}"
             f" · {analise['desconto']}% abaixo · "
             f"{analise['n']} carros comparáveis ({analise['nivel']})"]
    if old_price:
        lines.append(f"Baixou de {euros(old_price)}")
    if anuncio["avaliacao_site"]:
        lines.append(f"{NOMES[anuncio['origem']]}: {anuncio['avaliacao_site']}")
    if analise.get("fortes"):
        lines.append("A favor: " + ", ".join(analise["fortes"][:5]))
    if analise.get("alertas"):
        lines.append("Atenção: " + ", ".join(analise["alertas"][:4]))
    if avaria.get("tem"):
        lines.insert(2, f"⚠️ AVARIA: {', '.join(avaria['itens'][:4])}"
                        + (" (por identificar)" if avaria["incerta"] else "")
                        + " · margem sem a reparação, confirmar com mecânico")
    if analise["suspeito"]:
        lines.append("ATENÇÃO: muito abaixo do mercado. Confirma documentos e não pagues sinal antes de ver o carro.")
    return "\n".join(lines + [anuncio["url"]])


def processar(db, anuncio, config, referencia):
    """Guarda um anúncio e alerta se for negócio. Devolve True se já era conhecido."""
    if not anuncio["preco"] or not anuncio["titulo"]:
        return False
    anuncio["modelo"] = modelo_de(anuncio["titulo"])
    completar_motor(anuncio)
    row = db.execute("SELECT preco FROM anuncios WHERE origem = ? AND id = ?",
                     (anuncio["origem"], anuncio["id"])).fetchone()
    old_price = row[0] if row else None
    dropped = old_price and anuncio["preco"] <= old_price * (1 - config["descida_minima"])
    conhecido = row is not None and not anuncio["destaque"]
    if not referencia and (row is None or dropped) and interessa(anuncio, config):
        if candidato(db, anuncio, config):
            detalhe(anuncio)  # só os candidatos: lê a página completa
            anuncio["modelo"] = modelo_de(anuncio["titulo"])
            completar_motor(anuncio)
        analise = avaliar(db, anuncio, config)
        with ALERTA:  # as fontes correm em paralelo: o mesmo carro não pode ser alertado duas vezes
            alert = None
            if analise:
                if not duplicado(db, anuncio):  # o mesmo carro já alertado noutro site
                    alert = mensagem(anuncio, analise, old_price if dropped else None)
            elif config["alertar_todos"] and row is None and ENVIADOS_TODOS[0] < config["max_alertas_todos"]:
                ENVIADOS_TODOS[0] += 1  # ponytail: teto por janela de 30 min para o Telegram não bloquear o bot
                alert = mensagem_simples(anuncio)
            if alert:
                send(alert, anuncio["foto"])  # se falhar, nada é guardado e o anúncio é tentado no próximo ciclo
                print(f"{datetime.now():%H:%M:%S} Alerta ({NOMES[anuncio['origem']]}): {anuncio['titulo']}",
                      flush=True)
                guardar(db, anuncio, alertado=True)
                return conhecido
    guardar(db, anuncio, alertado=False)
    return conhecido


ALERTA = threading.Lock()


def guardar(db, anuncio, alertado):
    with db:
        db.execute(
            "INSERT INTO anuncios (origem, id, modelo, preco, ano, km, alertado, visto_em, titulo, url, local, "
            "foto, combustivel, caixa, profissional, importado, sinais, extras, cilindrada, potencia, carrocaria) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (origem, id) DO UPDATE SET preco = excluded.preco, modelo = excluded.modelo, "
            "ano = coalesce(excluded.ano, ano), km = coalesce(excluded.km, km), "
            "alertado = max(alertado, excluded.alertado), foto = coalesce(excluded.foto, foto), "
            "sinais = excluded.sinais, extras = coalesce(excluded.extras, extras), "
            "combustivel = coalesce(excluded.combustivel, combustivel), caixa = coalesce(excluded.caixa, caixa), "
            "cilindrada = coalesce(excluded.cilindrada, cilindrada), potencia = coalesce(excluded.potencia, potencia), "
            "carrocaria = coalesce(excluded.carrocaria, carrocaria)",
            (anuncio["origem"], anuncio["id"], anuncio["modelo"], anuncio["preco"], anuncio["ano"], anuncio["km"],
             int(alertado), datetime.now(UTC).isoformat(), anuncio["titulo"], anuncio["url"],
             anuncio["local"], anuncio["foto"], anuncio["combustivel"], anuncio["caixa"],
             anuncio["profissional"], anuncio["importado"],
             json.dumps(anuncio.get("sinais") or av.sinais_do_texto(anuncio["titulo"], anuncio["texto"])),
             json.dumps(anuncio["extras"]) if anuncio.get("extras") else None,
             anuncio.get("cilindrada"), anuncio.get("potencia"), anuncio.get("carrocaria")))


# ---------- ciclos ----------

def fonte_vazia(db, origem):
    return not db.execute("SELECT 1 FROM anuncios WHERE origem = ? LIMIT 1", (origem,)).fetchone()


def ciclo_paginado(db, origem, config, so_guardar=False):
    vazia = fonte_vazia(db, origem)
    referencia = so_guardar or vazia
    total = 0
    for pagina in range(1, (1 if vazia else config["paginas_max"]) + 1):
        anuncios = list(PAGINADAS[origem](pagina))
        total += len(anuncios)
        known = sum(processar(db, anuncio, config, referencia) for anuncio in anuncios)
        if known or not anuncios:
            break
    return total, referencia


def faixas(config):
    low, high = config["preco_min"], config["preco_max"]
    step = math.ceil((high - low) / config["facebook_faixas"])
    return [(start, min(high, start + step)) for start in range(low, high, step)]


def ciclo_facebook(db, config, so_guardar=False):
    referencia = so_guardar or fonte_vazia(db, "facebook")
    total = 0
    pesquisas = config["modelos"] or config["facebook_pesquisas"]
    for termo in pesquisas:
        for cidade in config["facebook_cidades"]:
            for low, high in faixas(config):
                anuncios = list(facebook(termo, cidade, low, high))
                total += len(anuncios)
                for anuncio in anuncios:
                    processar(db, anuncio, config, referencia)
                time.sleep(1)
    return total, referencia


LIMITE_PAGINAS = {"standvirtual": 1500, "olx": 25, "custojusto": 400}
COM_TETO = {"standvirtual", "olx"}  # aceitam filtro de preço no endereço


def explorar(db, config, aviso=print):
    """Lê páginas mais antigas aos poucos para o valor de mercado cobrir todos os modelos.

    Concentra-se nos carros até 2× o preço máximo: são esses os comparáveis dos negócios que interessam.
    """
    teto = config["preco_max"] * 2
    db.execute("CREATE TABLE IF NOT EXISTS estado (chave TEXT PRIMARY KEY, valor INTEGER)")
    for origem, limite_paginas in LIMITE_PAGINAS.items():
        if origem not in config["fontes"]:
            continue
        chave = f"explorar_{origem}"
        row = db.execute("SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
        pagina = row[0] if row else 2
        try:
            for _ in range(config["paginas_exploracao"]):
                anuncios = list(PAGINADAS[origem](pagina, **({"preco_max": teto} if origem in COM_TETO else {})))
                for anuncio in anuncios:
                    processar(db, anuncio, config, referencia=True)
                pagina = 2 if not anuncios or pagina >= limite_paginas else pagina + 1
        except (RuntimeError, KeyError, TypeError, ValueError) as error:  # site mudou: não pára o monitor
            aviso(f"Mercado {NOMES[origem]}: {error}")
        with db:
            db.execute("INSERT OR REPLACE INTO estado VALUES (?, ?)", (chave, pagina))
    for marca in config["marcas_foco"]:  # mercado mais fundo nas marcas em foco (ex.: Toyota)
        for origem, buscar in (("standvirtual", lambda p, m=marca: standvirtual(p, words(m).replace(" ", "-"), teto)),
                               ("olx", lambda p, m=marca: olx(p, words(m), teto))):
            if origem not in config["fontes"]:
                continue
            chave = f"foco_{origem}_{words(marca)}"
            row = db.execute("SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
            pagina = row[0] if row else 1
            try:
                anuncios = list(buscar(pagina))
                for anuncio in anuncios:
                    processar(db, anuncio, config, referencia=True)
                pagina = 1 if not anuncios or pagina >= 25 else pagina + 1
            except (RuntimeError, KeyError, TypeError, ValueError) as error:
                aviso(f"Mercado {marca} ({NOMES[origem]}): {error}")
            with db:
                db.execute("INSERT OR REPLACE INTO estado VALUES (?, ?)", (chave, pagina))


def semear(db, config, aviso=print):
    """Junta anúncios mais antigos (todas as marcas) para o valor de mercado existir desde o 1.º dia."""
    for origem in ("standvirtual", "olx"):
        if origem in config["fontes"] and fonte_vazia(db, origem):
            try:
                ciclo_paginado(db, origem, config)  # referência do feed antes da semente
            except (RuntimeError, KeyError, TypeError, ValueError) as error:
                aviso(f"Referência {NOMES[origem]}: {error}")
    if db.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0] < config["paginas_semente"] * 60:
        explorar(db, {**config, "paginas_exploracao": config["paginas_semente"]}, aviso)
    for termo in config["modelos"]:
        try:
            for pagina in (1, 2, 3):
                for anuncio in olx(pagina, termo):
                    processar(db, anuncio, config, referencia=True)
        except RuntimeError as error:
            aviso(f"Referência {termo}: {error}")
    total = db.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    aviso(f"Referência de mercado: {total} anúncios.")


# ---------- configuração ----------

PADROES = {
    "intervalo_segundos": 15, "paginas_max": 5, "paginas_semente": 15, "paginas_exploracao": 3,
    "preco_min": 300, "preco_max": 15000, "ano_min": 2005, "km_max": 250000, "km_ano_max": 20000,
    "desconto_telegram": 0.30, "desconto_foco": 0.15, "comparaveis_telegram": 10,
    "limite_burla": 0.5, "limite_irreal": 0.35, "min_comparaveis": 6, "descida_minima": 0.05,
    "custos_revenda": 500, "fator_venda": 0.95,
    "marcas_foco": ["toyota"], "pontuacao_minima": 75, "alertar_todos": False, "max_alertas_todos": 40,
    "apenas_nacionais": False, "modelos": [], "excluir": [], "excluir_texto": [],
    "fontes": ["standvirtual", "olx", "custojusto", "autopt", "autox", "facebook"],
    "facebook_cidades": ["lisbon", "porto", "braga", "coimbra", "faro", "viseu", "evora"], "facebook_pesquisas": ["carro"],
    "facebook_cada_minutos": 3, "facebook_faixas": 3,
}


def load_config(path):
    with path.open("rb") as file:
        config = {**PADROES, **tomllib.load(file)}
    if any(source not in NOMES for source in config["fontes"]):
        raise ValueError(f"Fontes válidas: {', '.join(NOMES)}.")
    for key in ("intervalo_segundos", "paginas_max", "paginas_semente", "paginas_exploracao", "min_comparaveis",
                "comparaveis_telegram", "facebook_cada_minutos", "facebook_faixas"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} tem de ser um número inteiro positivo.")
    if config["intervalo_segundos"] < 10:
        raise ValueError("Usa um intervalo de pelo menos 10 segundos para não seres bloqueado.")
    for key in ("desconto_telegram", "desconto_foco", "limite_burla", "limite_irreal", "descida_minima", "fator_venda"):
        if not 0 < config[key] <= 1:
            raise ValueError(f"{key} tem de estar entre 0 e 1 (ex.: 0.85).")
    if not 0 < config["preco_min"] < config["preco_max"]:
        raise ValueError("preco_min tem de ser menor do que preco_max.")
    if any(not words(term) for term in config["excluir"] + config["excluir_texto"] + config["modelos"]):
        raise ValueError("As listas de modelos/exclusões contêm um termo vazio.")
    return config


def abrir_db(path, **opcoes):
    db = sqlite3.connect(path, timeout=30, **opcoes)
    db.execute("PRAGMA journal_mode = WAL")  # várias threads a ler e escrever ao mesmo tempo
    db.execute("CREATE TABLE IF NOT EXISTS anuncios (origem TEXT, id TEXT, preco INTEGER, ano INTEGER, "
               "km INTEGER, alertado INTEGER DEFAULT 0, visto_em TEXT, titulo TEXT, url TEXT, local TEXT, "
               "PRIMARY KEY (origem, id))")
    colunas = {row[1] for row in db.execute("PRAGMA table_info(anuncios)")}
    for coluna in ("modelo", "foto", "combustivel", "caixa", "profissional", "importado", "sinais", "extras",
                   "cilindrada", "potencia", "carrocaria"):
        if coluna not in colunas:
            db.execute(f"ALTER TABLE anuncios ADD COLUMN {coluna}")
    db.execute("CREATE INDEX IF NOT EXISTS anuncios_modelo ON anuncios (modelo, ano)")
    versao = db.execute("PRAGMA user_version").fetchone()[0]
    antigos = db.execute("SELECT origem, id, titulo FROM anuncios WHERE titulo IS NOT NULL AND "
                         "(modelo IS NULL OR ? < 4)", (versao,)).fetchall()
    db.execute("PRAGMA user_version = 4")  # subir quando modelo_de muda: recalcula os modelos guardados
    if antigos:
        with db:
            db.executemany("UPDATE anuncios SET modelo = ? WHERE origem = ? AND id = ?",
                           [(modelo_de(t), o, i) for o, i, t in antigos])
    return db


def main():
    parser = argparse.ArgumentParser(description="Alertas de carros para o Telegram")
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--chat-id", action="store_true")
    mode.add_argument("--test-telegram", action="store_true")
    parser.add_argument("--minutos", type=float, help="vigia só durante N minutos (GitHub Actions) e sai")
    args = parser.parse_args()
    if args.chat_id:
        updates = telegram("getUpdates", timeout=5, allowed_updates=["message"])
        chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("title") or "conversa privada"
                 for u in updates if "message" in u}
        if not chats:
            raise ValueError("Envia /start ao bot (ou uma mensagem no grupo) e volta a correr --chat-id.")
        for chat_id, nome in chats.items():
            print(chat_id, nome)
        return 0
    if args.test_telegram:
        send("Alerta Carros: teste de ligação ao Telegram.")
        return 0
    secret("TELEGRAM_BOT_TOKEN")
    secret("TELEGRAM_CHAT_ID")
    if not args.config.is_file() and args.config == ROOT / "config.toml":
        args.config = ROOT / "config.exemplo.toml"  # sem config.toml próprio usa os critérios de exemplo
        print("A usar config.exemplo.toml (copia-o para config.toml para mudar os critérios).", flush=True)
    config = load_config(args.config)
    # a base fica sempre em config.sqlite3 (é esse o ficheiro guardado na cache do GitHub)
    caminho = (ROOT / "config.sqlite3" if args.config.name == "config.exemplo.toml"
               else args.config.resolve().with_suffix(".sqlite3"))
    db = abrir_db(caminho)
    semear(db, config)
    if args.once:  # GitHub Actions: uma passagem por todas as fontes
        failed = False
        for origem in config["fontes"]:
            try:
                total, referencia = (ciclo_facebook(db, config) if origem == "facebook"
                                     else ciclo_paginado(db, origem, config))
                print(f"{NOMES[origem]}: {total} anúncios lidos" + (" (referência inicial)" if referencia else ""),
                      flush=True)
            except (RuntimeError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
                print(f"{datetime.now():%H:%M:%S} {NOMES[origem]}: {error}", flush=True)
                failed = True
        explorar(db, config)
        db.close()  # fecha o WAL: a cache do GitHub guarda só o .sqlite3
        return int(failed)
    db.close()
    vigiar_tudo(caminho, config, args.minutos)
    return 0


def vigiar(origem, caminho, config, parar, so_guardar=False, erros=None):
    """Lê uma fonte sem parar, ao seu ritmo. Cada fonte tem a sua thread: uma lenta não atrasa as outras.

    so_guardar=True (painel): só junta anúncios, sem alertas. erros: dict onde ficam os erros por fonte.
    """
    erros = {} if erros is None else erros
    db = abrir_db(caminho)
    if origem == "facebook":
        intervalo = config["facebook_cada_minutos"] * 60
    elif origem == "mercado":
        intervalo = config["intervalo_segundos"] * 4
    else:
        intervalo = config["intervalo_segundos"]
    falhas, ciclos = 0, 0
    while not parar.is_set():
        inicio = time.monotonic()
        try:
            if origem == "mercado":
                explorar(db, config)
            else:
                total, referencia = (ciclo_facebook(db, config, so_guardar) if origem == "facebook"
                                     else ciclo_paginado(db, origem, config, so_guardar))
                if not so_guardar and (ciclos == 0 or referencia or falhas):
                    print(f"{datetime.now():%H:%M:%S} {NOMES[origem]}: {total} anúncios lidos"
                          + (" (referência inicial)" if referencia else ""), flush=True)
            falhas = 0
            erros.pop(origem, None)
        except (RuntimeError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
            falhas += 1
            erros[origem] = str(error)
            print(f"{datetime.now():%H:%M:%S} {NOMES.get(origem, origem)}: {error}", flush=True)
        ciclos += 1
        # com erros (bloqueio, site em baixo) espera cada vez mais, até 10 min, para não piorar
        espera = min(600, intervalo * 2 ** min(falhas, 6)) if falhas else intervalo
        parar.wait(max(1.0, espera - (time.monotonic() - inicio)))
    db.close()


def vigiar_tudo(caminho, config, minutos=None):
    """Vigia todas as fontes em paralelo. minutos: pára ao fim desse tempo (GitHub Actions) e fecha a base."""
    fim = time.monotonic() + minutos * 60 if minutos else None
    parar = threading.Event()
    threads = [threading.Thread(target=vigiar, args=(origem, caminho, config, parar), daemon=True, name=origem)
               for origem in [*config["fontes"], "mercado"]]
    for thread in threads:
        thread.start()
    print(f"A vigiar {', '.join(NOMES[o] for o in config['fontes'])} em paralelo "
          f"(de {config['intervalo_segundos']} em {config['intervalo_segundos']} s; "
          f"Facebook de {config['facebook_cada_minutos']} em {config['facebook_cada_minutos']} min).", flush=True)
    try:
        while fim is None or time.monotonic() < fim:
            parar.wait(1800 if fim is None else min(1800, max(1, fim - time.monotonic())))
            with ALERTA:
                ENVIADOS_TODOS[0] = 0  # max_alertas_todos vale por cada 30 min
    finally:
        parar.set()
        for thread in threads:
            thread.join(timeout=180)  # cada fonte acaba o ciclo em curso
        db = sqlite3.connect(caminho, timeout=60)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # tudo no .sqlite3: é esse o ficheiro guardado na cache
        db.close()
        print(f"{datetime.now():%H:%M:%S} Fim da vigia.", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from None
