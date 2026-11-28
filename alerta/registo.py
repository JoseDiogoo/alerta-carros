"""O que a vigia faz, para se saber sempre o que acontece. Tudo fica na própria base (e é publicado com ela):

- saude:    um resumo por site de 5 em 5 min (ciclos, falhas, anúncios novos, atraso face à publicação);
- decisoes: cada anúncio novo (ou que baixou de preço): quem o descobriu, com que atraso, e se alertou ou porquê não;
- eventos:  erros, reinícios e alarmes.

alarmes() diz o que está mal agora (site a falhar, site sem anúncios novos, publicação parada).
"""

import sqlite3
import statistics
import threading
import time
from datetime import UTC, datetime, timedelta

from .anuncio import NOMES

DIAS = {"saude": 14, "decisoes": 14, "eventos": 30}
# sites com anúncios novos a toda a hora: silêncio prolongado quer dizer que algo se partiu
COM_MOVIMENTO = ("standvirtual", "olx", "custojusto")

_LOCK = threading.Lock()
_CONTAS = {}  # origem -> contadores desde o último resumo
_ESTADO = {}  # origem -> {"a_falhar": resumos seguidos só com falhas, "ultimo_novo": time.monotonic()}
_INICIO = time.monotonic()


def preparar(db):
    db.execute("CREATE TABLE IF NOT EXISTS saude (quando TEXT, origem TEXT, ciclos INTEGER, falhas INTEGER, "
               "lidos INTEGER, novos INTEGER, atraso_min REAL, erro TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS decisoes (quando TEXT, origem TEXT, id TEXT, via TEXT, "
               "atraso_min REAL, resultado TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS eventos (quando TEXT, nivel TEXT, origem TEXT, mensagem TEXT)")
    db.execute("CREATE INDEX IF NOT EXISTS decisoes_anuncio ON decisoes (origem, id)")


def agora():
    return datetime.now(UTC).isoformat()


def atraso(anuncio):
    """Minutos entre a publicação no site e agora; None se o site não dá a data."""
    try:
        publicado = datetime.fromisoformat(anuncio.get("publicado_em") or "")
        return round((datetime.now(UTC) - publicado).total_seconds() / 60, 1)
    except (TypeError, ValueError):
        return None


def _conta(origem):
    return _CONTAS.setdefault(origem, {"ciclos": 0, "falhas": 0, "lidos": 0, "novos": 0, "atrasos": [], "erro": None})


def ciclo(origem, lidos=0, erro=None):
    with _LOCK:
        conta = _conta(origem)
        conta["ciclos"] += 1
        conta["lidos"] += lidos
        if erro:
            conta["falhas"] += 1
            conta["erro"] = str(erro)[:300]


def novo(origem, minutos):
    with _LOCK:
        conta = _conta(origem)
        conta["novos"] += 1
        if minutos is not None and 0 <= minutos <= 1440:
            conta["atrasos"].append(minutos)
        _ESTADO.setdefault(origem, {"a_falhar": 0})["ultimo_novo"] = time.monotonic()


def decisao(db, anuncio, via, resultado):
    with db:
        db.execute("INSERT INTO decisoes VALUES (?, ?, ?, ?, ?, ?)",
                   (agora(), anuncio["origem"], anuncio["id"], via, atraso(anuncio), resultado))


def evento(db, nivel, origem, mensagem):
    """Escreve no log e na base. Um registo que falha nunca pára a vigia."""
    print(f"{datetime.now():%H:%M:%S} [{nivel}] {NOMES.get(origem, origem)}: {mensagem}", flush=True)
    try:
        with db:
            db.execute("INSERT INTO eventos VALUES (?, ?, ?, ?)", (agora(), nivel, origem, str(mensagem)[:2000]))
    except sqlite3.Error as erro:
        print(f"Não consegui registar o evento: {erro}", flush=True)


def resumir(db):
    """Fecha o período: grava uma linha por site, imprime-a no log e apaga registos antigos."""
    with _LOCK:
        contas = dict(_CONTAS)
        _CONTAS.clear()
        for origem, c in contas.items():
            estado = _ESTADO.setdefault(origem, {"a_falhar": 0})
            estado["a_falhar"] = estado["a_falhar"] + 1 if c["falhas"] == c["ciclos"] and c["falhas"] else 0
    momento = agora()
    with db:
        for origem, c in sorted(contas.items()):
            mediana = round(statistics.median(c["atrasos"]), 1) if c["atrasos"] else None
            db.execute("INSERT INTO saude VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (momento, origem, c["ciclos"], c["falhas"], c["lidos"], c["novos"], mediana, c["erro"]))
            print(f"{datetime.now():%H:%M:%S} Resumo {NOMES.get(origem, origem)}: {c['ciclos']} ciclos, "
                  f"{c['falhas']} falhas, {c['lidos']} lidos, {c['novos']} novos"
                  + (f", atraso mediano {mediana} min" if mediana is not None else "")
                  + (f", último erro: {c['erro']}" if c["erro"] else ""), flush=True)
        for tabela, dias in DIAS.items():
            limite = (datetime.now(UTC) - timedelta(days=dias)).isoformat()
            db.execute(f"DELETE FROM {tabela} WHERE quando < ?", (limite,))


def alarmes(config):
    """{chave: texto} do que está mal agora."""
    mal = {}
    with _LOCK:
        for origem in config["fontes"]:
            estado = _ESTADO.get(origem, {})
            nome = NOMES.get(origem, origem)
            if estado.get("a_falhar", 0) >= 3:
                mal[f"falhas:{origem}"] = f"{nome} falha há pelo menos 15 minutos seguidos."
            silencio = (time.monotonic() - estado.get("ultimo_novo", _INICIO)) / 60
            if origem in COM_MOVIMENTO and silencio >= config["alarme_sem_novos_minutos"]:
                mal[f"silencio:{origem}"] = f"{nome} sem anúncios novos há mais de {config['alarme_sem_novos_minutos']} min."
    return mal
