"""Pedidos HTTP aos sites e às APIs."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "text/html", "Accept-Language": "pt-PT,pt", "Sec-Fetch-Mode": "navigate",
}


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


def next_data(page):
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not match:
        raise RuntimeError("página sem dados de anúncios (o site mudou?)")
    return json.loads(match.group(1))
