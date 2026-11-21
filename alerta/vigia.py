"""Vigia dos sites: lê cada fonte ao seu ritmo, guarda e alerta."""

import math
import sqlite3
import threading
import time
from datetime import datetime

from .anuncio import NOMES, completar_motor
from .db import abrir_db, duplicado, fonte_vazia, guardar, limpar, vale_guardar
from .fontes import PAGINADAS, detalhe
from .fontes.facebook import facebook
from .fontes.olx import olx
from .fontes.standvirtual import standvirtual
from .negocio import avaliar, candidato, interessa
from .telegram import mensagem, mensagem_simples, send
from .texto import modelo_de, words


ENVIADOS_TODOS = [0]


ALERTA = threading.Lock()


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
    if not vale_guardar(anuncio, config):
        return conhecido  # não é carro ou é caro de mais para servir de comparação: não ocupa a base
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
    for cidade in config["facebook_cidades"]:
        for low, high in faixas(config):
            anuncios = list(facebook(cidade, low, high))
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
    falhas, ciclos, limpeza = 0, 0, time.monotonic()
    while not parar.is_set():
        inicio = time.monotonic()
        try:
            if origem == "mercado":
                explorar(db, config)
                if time.monotonic() - limpeza > 6 * 3600:  # regras da base também numa vigia longa (painel)
                    limpar(db, config)
                    limpeza = time.monotonic()
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
