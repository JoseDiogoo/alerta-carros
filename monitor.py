"""Alertas de carros bons e baratos no Telegram, lidos diretamente dos sites (sem serviços pagos)."""

import argparse
import html as htmllib
import json
import math
import os
import re
import sqlite3
import statistics
import time
import tomllib
import unicodedata
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


def send(message, foto=None):
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
    marca, resto = MARCAS_ALIAS.get(tokens[0], tokens[0]), tokens[1:]
    while resto and resto[0] in {"benz", "romeo", "rover"}:
        resto = resto[1:]
    if not resto:
        return None
    modelo = f"{marca} {resto[0]}"
    if resto[0] in {"model", "serie", "series", "classe", "class", "grand", "range"} and len(resto) > 1:
        modelo += f" {resto[1]}"
    desportiva = next((t for t in resto[1:] if t in VERSOES_DESPORTIVAS), None)
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
            "combustivel": None, "caixa": None, **extra}


# ---------- fontes ----------

def standvirtual(pagina, caminho=""):
    """caminho opcional "marca/modelo", ex.: "toyota/yaris"."""
    data = next_data(descarregar(f"https://www.standvirtual.com/carros/{caminho}?search%5Border%5D="
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
                    combustivel=combustivel(params.get("fuel_type")), caixa=caixa(params.get("gearbox")))


def olx(pagina, termo=None):
    base = "https://www.olx.pt/carros-motos-e-barcos/carros/"
    if termo:
        base += f"q-{quote(termo.replace(' ', '-'))}/"
    page = descarregar(f"{base}?search%5Border%5D=created_at:desc&page={pagina}")
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
                    combustivel=combustivel(params.get("combustivel")), caixa=caixa(params.get("gearbox")))


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
                           texto=data.get("description") or "")
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
    """Valor de mercado: mediana do mesmo modelo, ano ±1 e, se houver amostra, km parecidos."""
    if not (anuncio.get("modelo") and anuncio["ano"]):
        return None, 0
    base = ("SELECT preco FROM anuncios WHERE modelo = ? AND ano BETWEEN ? AND ? "
            "AND NOT (origem = ? AND id = ?) AND preco >= 300")
    args = [anuncio["modelo"], anuncio["ano"] - 1, anuncio["ano"] + 1, anuncio["origem"], anuncio["id"]]
    if anuncio["km"]:  # só compara com km parecidos: sem amostra não há estimativa (melhor que um valor errado)
        margem_km = max(30000, anuncio["km"] * 0.35)
        base += " AND km BETWEEN ? AND ?"
        args += [anuncio["km"] - margem_km, anuncio["km"] + margem_km]
    prices = [r[0] for r in db.execute(base, args)]
    return (statistics.median(prices) if len(prices) >= minimo else None), len(prices)


def avaliar(db, anuncio, config):
    """Análise do negócio, ou None se não passar os filtros/margem."""
    if not (interessa(anuncio, config) and dentro_dos_limites(anuncio, config)):
        return None
    mediana, n = mercado(db, anuncio, config["min_comparaveis"])
    if n < config["min_comparaveis"]:
        return None
    ratio = anuncio["preco"] / mediana
    margem = mediana - anuncio["preco"] - config["custos_revenda"]
    if ratio > config["desconto_mercado"] or margem < config["margem_minima"]:
        return None
    return {"mediana": round(mediana), "n": n, "desconto": round((1 - ratio) * 100),
            "margem": round(margem), "suspeito": ratio < config["limite_burla"]}


def duplicado(db, anuncio):
    if not (anuncio["km"] and anuncio["ano"]):
        return False
    return db.execute(
        "SELECT 1 FROM anuncios WHERE alertado = 1 AND modelo = ? AND ano = ? AND km = ? "
        "AND ABS(preco - ?) <= 250 AND origem != ?",
        (anuncio["modelo"], anuncio["ano"], anuncio["km"], anuncio["preco"], anuncio["origem"])).fetchone() is not None


def euros(valor):
    return f"{int(valor):,} €".replace(",", " ")


def mensagem(anuncio, analise, old_price):
    facts = [euros(anuncio["preco"])]
    if anuncio["ano"]:
        facts.append(str(anuncio["ano"]))
    if anuncio["km"]:
        facts.append(f"{anuncio['km']:,} km".replace(",", " "))
    for extra in (anuncio["combustivel"], anuncio["caixa"]):
        if extra:
            facts.append(extra)
    if anuncio["profissional"] is not None:
        facts.append("profissional" if anuncio["profissional"] else "particular")
    if anuncio["importado"]:
        facts.append("importado")
    header = f"{anuncio['modelo'].title()} · {NOMES[anuncio['origem']]}"
    if anuncio["local"]:
        header += f" · {anuncio['local'][:60]}"
    lines = [header, anuncio["titulo"][:150], " · ".join(facts),
             f"Margem estimada: +{euros(analise['margem'])}",
             f"Mercado ~{euros(analise['mediana'])} ({analise['n']} anúncios) · {analise['desconto']}% abaixo"]
    if old_price:
        lines.append(f"Baixou de {euros(old_price)}")
    if anuncio["avaliacao_site"]:
        lines.append(f"{NOMES[anuncio['origem']]}: {anuncio['avaliacao_site']}")
    if analise["suspeito"]:
        lines.append("ATENÇÃO: muito abaixo do mercado. Confirma documentos e não pagues sinal antes de ver o carro.")
    return "\n".join(lines + [anuncio["url"]])


def processar(db, anuncio, config, referencia):
    """Guarda um anúncio e alerta se for negócio. Devolve True se já era conhecido."""
    if not anuncio["preco"] or not anuncio["titulo"]:
        return False
    anuncio["modelo"] = modelo_de(anuncio["titulo"])
    row = db.execute("SELECT preco FROM anuncios WHERE origem = ? AND id = ?",
                     (anuncio["origem"], anuncio["id"])).fetchone()
    old_price = row[0] if row else None
    dropped = old_price and anuncio["preco"] <= old_price * (1 - config["descida_minima"])
    alert = None
    if not referencia and (row is None or dropped) and interessa(anuncio, config):
        if (anuncio["origem"] == "autopt" and anuncio["km"] is None
                and config["preco_min"] <= anuncio["preco"] <= config["preco_max"]):
            completar_autopt(anuncio)
            anuncio["modelo"] = modelo_de(anuncio["titulo"])
        analise = avaliar(db, anuncio, config)
        if analise and not duplicado(db, anuncio):
            alert = mensagem(anuncio, analise, old_price if dropped else None)
            send(alert, anuncio["foto"])  # se falhar, nada é guardado e o anúncio é tentado no próximo ciclo
            print(f"{datetime.now():%H:%M:%S} Alerta ({NOMES[anuncio['origem']]}): {anuncio['titulo']}", flush=True)
    with db:
        db.execute(
            "INSERT INTO anuncios (origem, id, modelo, preco, ano, km, alertado, visto_em, titulo, url, local, "
            "foto, combustivel, caixa, profissional, importado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (origem, id) DO UPDATE SET preco = excluded.preco, modelo = excluded.modelo, "
            "ano = coalesce(excluded.ano, ano), km = coalesce(excluded.km, km), "
            "alertado = max(alertado, excluded.alertado), foto = coalesce(excluded.foto, foto)",
            (anuncio["origem"], anuncio["id"], anuncio["modelo"], anuncio["preco"], anuncio["ano"], anuncio["km"],
             int(alert is not None), datetime.now(UTC).isoformat(), anuncio["titulo"], anuncio["url"],
             anuncio["local"], anuncio["foto"], anuncio["combustivel"], anuncio["caixa"],
             anuncio["profissional"], anuncio["importado"]))
    return row is not None and not anuncio["destaque"]


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


def ciclo_facebook(db, config):
    referencia = fonte_vazia(db, "facebook")
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


def explorar(db, config, aviso=print):
    """Lê páginas mais antigas aos poucos para o valor de mercado cobrir todos os modelos."""
    db.execute("CREATE TABLE IF NOT EXISTS estado (chave TEXT PRIMARY KEY, valor INTEGER)")
    for origem, limite_paginas in LIMITE_PAGINAS.items():
        if origem not in config["fontes"]:
            continue
        chave = f"explorar_{origem}"
        row = db.execute("SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
        pagina = row[0] if row else 2
        try:
            for _ in range(config["paginas_exploracao"]):
                anuncios = list(PAGINADAS[origem](pagina))
                for anuncio in anuncios:
                    processar(db, anuncio, config, referencia=True)
                pagina = 2 if not anuncios or pagina >= limite_paginas else pagina + 1
        except RuntimeError as error:
            aviso(f"Mercado {NOMES[origem]}: {error}")
        with db:
            db.execute("INSERT OR REPLACE INTO estado VALUES (?, ?)", (chave, pagina))


def semear(db, config, aviso=print):
    """Junta anúncios mais antigos (todas as marcas) para o valor de mercado existir desde o 1.º dia."""
    for origem in ("standvirtual", "olx"):
        if origem in config["fontes"] and fonte_vazia(db, origem):
            ciclo_paginado(db, origem, config)  # referência do feed antes da semente
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
    "intervalo_segundos": 60, "paginas_max": 5, "paginas_semente": 15, "paginas_exploracao": 3,
    "preco_min": 300, "preco_max": 15000, "ano_min": 2005, "km_max": 250000, "km_ano_max": 20000,
    "desconto_mercado": 0.85, "limite_burla": 0.5, "min_comparaveis": 6, "descida_minima": 0.05,
    "margem_minima": 800, "custos_revenda": 500,
    "apenas_nacionais": False, "modelos": [], "excluir": [], "excluir_texto": [],
    "fontes": ["standvirtual", "olx", "custojusto", "autopt", "autox", "facebook"],
    "facebook_cidades": ["lisbon"], "facebook_pesquisas": ["carro"],
    "facebook_cada_minutos": 10, "facebook_faixas": 3,
}


def load_config(path):
    with path.open("rb") as file:
        config = {**PADROES, **tomllib.load(file)}
    if any(source not in NOMES for source in config["fontes"]):
        raise ValueError(f"Fontes válidas: {', '.join(NOMES)}.")
    for key in ("intervalo_segundos", "paginas_max", "paginas_semente", "paginas_exploracao", "min_comparaveis",
                "facebook_cada_minutos", "facebook_faixas"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} tem de ser um número inteiro positivo.")
    if config["intervalo_segundos"] < 30:
        raise ValueError("Usa um intervalo de pelo menos 30 segundos para não seres bloqueado.")
    for key in ("desconto_mercado", "limite_burla", "descida_minima"):
        if not 0 < config[key] < 1:
            raise ValueError(f"{key} tem de estar entre 0 e 1 (ex.: 0.85).")
    if not 0 < config["preco_min"] < config["preco_max"]:
        raise ValueError("preco_min tem de ser menor do que preco_max.")
    if any(not words(term) for term in config["excluir"] + config["excluir_texto"] + config["modelos"]):
        raise ValueError("As listas de modelos/exclusões contêm um termo vazio.")
    return config


def abrir_db(path, **opcoes):
    db = sqlite3.connect(path, timeout=20, **opcoes)
    db.execute("CREATE TABLE IF NOT EXISTS anuncios (origem TEXT, id TEXT, preco INTEGER, ano INTEGER, "
               "km INTEGER, alertado INTEGER DEFAULT 0, visto_em TEXT, titulo TEXT, url TEXT, local TEXT, "
               "PRIMARY KEY (origem, id))")
    colunas = {row[1] for row in db.execute("PRAGMA table_info(anuncios)")}
    for coluna in ("modelo", "foto", "combustivel", "caixa", "profissional", "importado"):
        if coluna not in colunas:
            db.execute(f"ALTER TABLE anuncios ADD COLUMN {coluna}")
    db.execute("CREATE INDEX IF NOT EXISTS anuncios_modelo ON anuncios (modelo, ano)")
    versao = db.execute("PRAGMA user_version").fetchone()[0]
    antigos = db.execute("SELECT origem, id, titulo FROM anuncios WHERE titulo IS NOT NULL AND "
                         "(modelo IS NULL OR ? < 2)", (versao,)).fetchall()
    db.execute("PRAGMA user_version = 2")
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
    config = load_config(args.config)
    db = abrir_db(args.config.resolve().with_suffix(".sqlite3"))
    semear(db, config)
    last_facebook = 0.0
    while True:
        failed = False
        for origem in config["fontes"]:
            if origem == "facebook":
                if time.monotonic() - last_facebook < config["facebook_cada_minutos"] * 60:
                    continue
                last_facebook = time.monotonic()
            try:
                total, referencia = (ciclo_facebook(db, config) if origem == "facebook"
                                     else ciclo_paginado(db, origem, config))
                if referencia:
                    print(f"{NOMES[origem]}: {total} anúncios guardados como referência inicial.", flush=True)
            except (RuntimeError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
                print(f"{datetime.now():%H:%M:%S} {NOMES[origem]}: {error}", flush=True)
                failed = True
        explorar(db, config)
        if args.once:
            return int(failed)
        time.sleep(config["intervalo_segundos"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from None
