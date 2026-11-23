"""Vigia dos sites: lê cada fonte ao seu ritmo, guarda e alerta."""

import math
import random
import os
import subprocess
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from .anuncio import NOMES, completar_motor
from .catalogo import identificar
from .db import abrir_db, duplicado, fonte_vazia, guardar, limpar, reidentificar, vale_guardar
from .fontes import PAGINADAS, detalhe
from .catalogo import slug
from .fontes.facebook import SessaoFacebook, facebook
from .fontes.http import ainda_existe
from .fontes.olx import olx
from .fontes.standvirtual import standvirtual
from .negocio import apenas_foco, avaliar, candidato, de_foco, interessa
from .telegram import mensagem, mensagem_simples, send
from .texto import words


ENVIADOS_TODOS = [0]


ALERTA = threading.Lock()


def processar(db, anuncio, config, referencia):
    """Guarda um anúncio e alerta se for negócio. Devolve True se já era conhecido."""
    if not anuncio["preco"] or not anuncio["titulo"]:
        return False
    identificar(db, anuncio)  # marca e modelo: campos do site ou catálogo; na dúvida, None
    completar_motor(anuncio)
    row = db.execute("SELECT preco FROM anuncios WHERE origem = ? AND id = ?",
                     (anuncio["origem"], anuncio["id"])).fetchone()
    old_price = row[0] if row else None
    dropped = old_price and anuncio["preco"] <= old_price * (1 - config["descida_minima"])
    conhecido = row is not None and not anuncio["destaque"]
    if not vale_guardar(anuncio, config):
        return conhecido  # não é carro ou é caro de mais para servir de comparação: não ocupa a base
    lido = False
    if not referencia and (row is None or dropped) and anuncio["origem"] == "autopt" and not anuncio["modelo"]:
        detalhe(anuncio)  # a lista do Auto.pt não traz marca nem modelo: estão na página do anúncio
        identificar(db, anuncio)
        lido = True
    if not referencia and (row is None or dropped) and interessa(anuncio, config):
        if not lido and candidato(db, anuncio, config):
            detalhe(anuncio)  # só os candidatos: lê a página completa
            identificar(db, anuncio)
            completar_motor(anuncio)
        analise = avaliar(db, anuncio, config)
        with ALERTA:  # as fontes correm em paralelo: o mesmo carro não pode ser alertado duas vezes
            alert = None
            if analise:
                if not duplicado(db, anuncio):  # o mesmo carro já alertado noutro site
                    alert = mensagem(anuncio, analise, old_price if dropped else None)
            elif config["alertar_todos"] and row is None and (not apenas_foco(config) or de_foco(anuncio, config)) and ENVIADOS_TODOS[0] < config["max_alertas_todos"]:
                ENVIADOS_TODOS[0] += 1  # ponytail: teto por janela de 30 min para o Telegram não bloquear o bot
                alert = mensagem_simples(anuncio)
            if alert:
                try:
                    send(alert, anuncio["foto"])
                except RuntimeError as error:  # timeout ou 429: a página continua; o anúncio não é guardado
                    print(f"{datetime.now():%H:%M:%S} Telegram falhou ({error}); volta a tentar no próximo ciclo.",
                          flush=True)
                    return False
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
            time.sleep(random.uniform(15, 45))  # ritmo humano: com sessão de conta, rajadas dão bloqueio
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


def verificar(db, config, lote=60):
    """Marca como removidos os anúncios que o site já não tem (o painel esconde-os; continuam a contar no mercado).

    Percorre primeiro as marcas em foco e os que há mais tempo não foram vistos. O Facebook não se verifica
    (abrir o anúncio sem sessão leva ao login). Devolve quantos acabou de marcar como removidos.
    """
    foco = [slug(m) for m in config["marcas_foco"]]
    antigos = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    linhas = db.execute(
        "SELECT origem, id, url FROM anuncios WHERE origem != 'facebook' AND (removido IS NULL OR removido = 0) "
        "AND preco BETWEEN ? AND ? AND (verificado_em IS NULL OR verificado_em < ?) "
        f"ORDER BY marca IN ({','.join('?' * len(foco))}) DESC, verificado_em IS NOT NULL, verificado_em LIMIT ?",
        (config["preco_min"], config["preco_max"], antigos, *foco, lote)).fetchall()
    with ThreadPoolExecutor(6) as pool:
        resultados = list(pool.map(lambda linha: ainda_existe(linha[2]), linhas))
    agora = datetime.now(UTC).isoformat()
    removidos = 0
    with db:
        for (origem, id_, _), existe in zip(linhas, resultados):
            if existe is not None:  # None = não deu para saber (site em baixo): tenta noutro ciclo
                db.execute("UPDATE anuncios SET removido = ?, verificado_em = ? WHERE origem = ? AND id = ?",
                           (int(not existe), agora, origem, id_))
                removidos += not existe
    return removidos


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
    elif origem == "verificar":
        intervalo = 60
    else:
        intervalo = config["intervalo_segundos"]
    falhas, ciclos, limpeza, reidentificado = 0, 0, time.monotonic(), time.monotonic()
    while not parar.is_set():
        inicio = time.monotonic()
        try:
            if origem == "verificar":
                verificar(db, config)
            elif origem == "mercado":
                explorar(db, config)
                if time.monotonic() - limpeza > 6 * 3600:  # regras da base também numa vigia longa (painel)
                    limpar(db, config)
                    limpeza = time.monotonic()
                elif time.monotonic() - reidentificado > 600:  # o catálogo cresce: dá modelo a quem não tinha
                    reidentificar(db)
                    reidentificado = time.monotonic()
            else:
                total, referencia = (ciclo_facebook(db, config, so_guardar) if origem == "facebook"
                                     else ciclo_paginado(db, origem, config, so_guardar))
                if not so_guardar and (ciclos == 0 or referencia or falhas):
                    print(f"{datetime.now():%H:%M:%S} {NOMES[origem]}: {total} anúncios lidos"
                          + (" (referência inicial)" if referencia else ""), flush=True)
            falhas = 0
            erros.pop(origem, None)
        except SessaoFacebook as error:
            # sessão morta: insistir só arrisca a conta. Avisa uma vez e pára o Facebook até à próxima execução.
            erros[origem] = str(error)
            print(f"{datetime.now():%H:%M:%S} Facebook: {error}. Parado até à próxima execução.", flush=True)
            if not so_guardar:
                try:
                    send("Alerta Carros: o Facebook pediu login. Renova o secret FACEBOOK_COOKIES "
                         "(c_user, xs, datr) no GitHub.")
                except RuntimeError:
                    pass
            break
        except (RuntimeError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
            falhas += 1
            erros[origem] = str(error)
            print(f"{datetime.now():%H:%M:%S} {NOMES.get(origem, origem)}: {error}", flush=True)
        ciclos += 1
        # com erros (bloqueio, site em baixo) espera cada vez mais, até 10 min, para não piorar
        espera = min(600, intervalo * 2 ** min(falhas, 6)) if falhas else intervalo
        parar.wait(max(1.0, espera - (time.monotonic() - inicio)))
    db.close()


def publicar(caminho):
    """Checkpoint: cópia coerente da base para o GitHub Release "dados" (sobrevive a um runner cancelado e é
    o que o painel lê). Só corre se PUBLICAR_BASE estiver definido (GitHub Actions)."""
    pasta = caminho.parent / "publicar"
    pasta.mkdir(exist_ok=True)
    copia = pasta / "base.sqlite3"  # o nome do ficheiro é o nome do ficheiro no Release
    with sqlite3.connect(caminho, timeout=60) as origem, sqlite3.connect(copia) as destino:
        origem.backup(destino)  # cópia consistente com as threads a escrever
        destino.execute("PRAGMA journal_mode = DELETE")  # sem ficheiros -wal: abre-se só para leitura
    try:  # falhar a publicar nunca pára a vigia: tenta outra vez daqui a 30 min
        resultado = subprocess.run(["gh", "release", "upload", "dados", str(copia), "--clobber"],
                                   capture_output=True, text=True, timeout=300)
        erro = resultado.stderr.strip()[:200] if resultado.returncode else None
    except (OSError, subprocess.TimeoutExpired) as error:
        erro = str(error)
    print(f"{datetime.now():%H:%M:%S} " + (f"Falhou a publicar a base: {erro}" if erro else "Base publicada"),
          flush=True)


def vigiar_tudo(caminho, config, minutos=None):
    """Vigia todas as fontes em paralelo. minutos: pára ao fim desse tempo (GitHub Actions) e fecha a base."""
    fim = time.monotonic() + minutos * 60 if minutos else None
    parar = threading.Event()
    threads = [threading.Thread(target=vigiar, args=(origem, caminho, config, parar), daemon=True, name=origem)
               for origem in [*config["fontes"], "mercado", "verificar"]]
    for thread in threads:
        thread.start()
    print(f"A vigiar {', '.join(NOMES[o] for o in config['fontes'])} em paralelo "
          f"(de {config['intervalo_segundos']} em {config['intervalo_segundos']} s; "
          f"Facebook de {config['facebook_cada_minutos']} em {config['facebook_cada_minutos']} min).", flush=True)
    try:
        tique = 0
        while fim is None or time.monotonic() < fim:
            parar.wait(300 if fim is None else min(300, max(1, fim - time.monotonic())))
            tique += 1
            if tique % 6 == 0:  # max_alertas_todos vale por cada 30 min
                with ALERTA:
                    ENVIADOS_TODOS[0] = 0
            if os.environ.get("PUBLICAR_BASE"):
                publicar(caminho)  # checkpoint de 5 em 5 min: o painel está sempre a ≤5 min do Telegram
    finally:
        parar.set()
        for thread in threads:
            thread.join(timeout=180)  # cada fonte acaba o ciclo em curso
        db = sqlite3.connect(caminho, timeout=60)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # tudo no .sqlite3: é esse o ficheiro guardado na cache
        db.close()
        if os.environ.get("PUBLICAR_BASE"):
            publicar(caminho)
        print(f"{datetime.now():%H:%M:%S} Fim da vigia.", flush=True)
