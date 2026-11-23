"""Painel: a base de dados que a vigia (GitHub Actions) publica no Release "dados", descarregada quando muda."""

import sqlite3
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = "https://github.com/JoseDiogoo/alerta-carros/releases/download/dados/base.sqlite3"


def versao(url=URL):
    """Identifica a versão publicada (ETag, ou data+tamanho) sem a descarregar; None se não existir."""
    try:
        with urlopen(Request(url, method="HEAD"), timeout=15) as resposta:
            return (resposta.headers.get("ETag") or
                    f"{resposta.headers.get('Last-Modified')}-{resposta.headers.get('Content-Length')}")
    except (HTTPError, URLError, OSError):
        return None


def descarregar(destino, url=URL):
    """Descarrega para um ficheiro novo, confirma que é uma base inteira e abre-a só para leitura."""
    destino = Path(destino)
    parcial = destino.with_suffix(".parcial")
    with urlopen(Request(url), timeout=120) as resposta, parcial.open("wb") as ficheiro:
        while bloco := resposta.read(1 << 20):
            ficheiro.write(bloco)
    verificacao = sqlite3.connect(parcial)
    try:
        if verificacao.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("base descarregada com erros")
    finally:
        verificacao.close()
    parcial.replace(destino)
    return abrir(destino)


def abrir(caminho):
    return sqlite3.connect(f"file:{Path(caminho).as_posix()}?mode=ro", uri=True, check_same_thread=False)
