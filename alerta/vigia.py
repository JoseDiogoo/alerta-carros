"""Vigia dos sites: lê cada fonte ao seu ritmo, guarda e alerta."""

import math
import random
import os
import subprocess
import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from . import avaliacao as av
from .anuncio import NOMES, carro, completar_motor
from .catalogo import identificar
from .db import abrir_db, duplicado, fonte_vazia, guardar, limpar, reidentificar, vale_guardar
from . import registo
from .fontes import COMPLETAR, PAGINADAS, detalhe
from .catalogo import slug
from .fontes.facebook import ULTIMA, SessaoFacebook, facebook
from .fontes.http import ainda_existe
from .fontes.olx import olx
from .fontes.standvirtual import standvirtual
from .negocio import apenas_foco, avaliar, candidato, de_foco, interessa, porque_nao
from .telegram import mensagem, mensagem_simples, send
from .texto import euros, km_plausivel, words


ENVIADOS_TODOS = [0]


ALERTA = threading.Lock()


def recente(anuncio, config):
    """Publicado há tão pouco tempo que ainda é novidade, mesmo que tenha sido a leitura do mercado a achá-lo."""
    minutos = registo.atraso(anuncio)
    return minutos is not None and minutos <= config["alerta_exploracao_minutos"]


def processar(db, anuncio, config, referencia, via="feed", alertar_recentes=False):
    """Guarda um anúncio e alerta se for negócio. Devolve True se já era conhecido.

    referencia: só serve para o mercado (sem alerta), exceto se alertar_recentes e o anúncio for acabado de publicar.
    """
    if not anuncio["preco"] or not anuncio["titulo"]:
        return False
    anuncio["km"] = km_plausivel(anuncio["km"], anuncio["ano"])
    identificar(db, anuncio)  # marca e modelo: campos do site ou catálogo; na dúvida, None
    completar_motor(anuncio)
    row = db.execute("SELECT preco FROM anuncios WHERE origem = ? AND id = ?",
                     (anuncio["origem"], anuncio["id"])).fetchone()
    old_price = row[0] if row else None
    dropped = old_price and anuncio["preco"] <= old_price * (1 - config["descida_minima"])
    conhecido = row is not None and not anuncio["destaque"]
    if not vale_guardar(anuncio, config):
        return conhecido  # não é carro ou é caro de mais para servir de comparação: não ocupa a base
    if referencia and alertar_recentes and row is None and recente(anuncio, config):
        referencia = False
    if row is None and not referencia:
        registo.novo(anuncio["origem"], registo.atraso(anuncio))
    avalia = not referencia and bool(row is None or dropped)
    lido = False
    # Auto.pt: a lista não traz marca nem modelo. CustoJusto: a lista não traz os km. Estão na página do anúncio.
    if avalia and ((anuncio["origem"] == "autopt" and not anuncio["modelo"])
                   or (anuncio["origem"] == "custojusto" and anuncio["preco"] <= config["preco_max"])):
        detalhe(anuncio)
        identificar(db, anuncio)
        lido = True
    resultado = None
    if avalia and interessa(anuncio, config):
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
                else:
                    resultado = "já alertado noutro site"
            elif config["alertar_todos"] and row is None and (not apenas_foco(config) or de_foco(anuncio, config)) and ENVIADOS_TODOS[0] < config["max_alertas_todos"]:
                ENVIADOS_TODOS[0] += 1  # ponytail: teto por janela de 30 min para o Telegram não bloquear o bot
                alert = mensagem_simples(anuncio)
            if alert:
                try:
                    send(alert, anuncio["foto"])
                except RuntimeError as error:  # timeout ou 429: a página continua; o anúncio não é guardado
                    registo.evento(db, "erro", anuncio["origem"],
                                   f"Telegram falhou ({error}); volta a tentar no próximo ciclo.")
                    return False
                print(f"{datetime.now():%H:%M:%S} Alerta ({NOMES[anuncio['origem']]}): {anuncio['titulo']}",
                      flush=True)
                guardar(db, anuncio, alertado=True)
                registo.decisao(db, anuncio, via, "alerta")
                return conhecido
    guardar(db, anuncio, alertado=False)
    if avalia and anuncio["preco"] <= config["preco_max"]:
        registo.decisao(db, anuncio, via, resultado or porque_nao(db, anuncio, config))
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
    if origem in config["feed_com_teto"] and not vazia:
        # a pesquisa com teto de preço é outra página na cache do site: costuma trazer anúncios mais cedo
        anuncios = list(PAGINADAS[origem](1, preco_max=config["preco_max"]))
        total += len(anuncios)
        for anuncio in anuncios:
            processar(db, anuncio, config, referencia, via="feed com teto")
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
    if total == 0:  # nem uma cidade deu anúncios: não é "0 novos", é uma página sem dados (bloqueio silencioso)
        raise RuntimeError(f"Facebook devolveu 0 anúncios em todas as pesquisas ({ULTIMA})")
    return total, referencia


LIMITE_PAGINAS = {"standvirtual": 1500, "olx": 25, "custojusto": 400}


COM_TETO = {"standvirtual", "olx"}  # aceitam filtro de preço no endereço


def explorar(db, config, aviso=print, alertar_recentes=False):
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
                    processar(db, anuncio, config, True, "mercado", alertar_recentes)
                pagina = 2 if not anuncios or pagina >= limite_paginas else pagina + 1
        except (RuntimeError, KeyError, TypeError, ValueError) as error:  # site mudou: não pára o monitor
            aviso(f"Mercado {NOMES[origem]}: {error}")
            registo.ciclo("mercado", erro=f"{NOMES[origem]}: {error}")
            pagina = 2 if pagina >= limite_paginas else pagina + 1  # uma página estragada não prende a leitura
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
                    processar(db, anuncio, config, True, "mercado em foco", alertar_recentes)
                pagina = 1 if not anuncios or pagina >= 25 else pagina + 1
            except (RuntimeError, KeyError, TypeError, ValueError) as error:
                aviso(f"Mercado {marca} ({NOMES[origem]}): {error}")
                registo.ciclo("mercado", erro=f"{marca} {NOMES[origem]}: {error}")
                pagina = 1 if pagina >= 25 else pagina + 1
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
        f"ORDER BY coalesce(marca, '') IN ({','.join('?' * len(foco))}) DESC, verificado_em IS NOT NULL, verificado_em LIMIT ?",
        (config["preco_min"], config["preco_max"], antigos, *foco, lote)).fetchall()
    with ThreadPoolExecutor(3) as pool:
        resultados = list(pool.map(lambda linha: ainda_existe(linha[2]), linhas))
    agora = datetime.now(UTC).isoformat()
    # sem resposta: volta ao fim da fila e repete daqui a 30 min (senão os mesmos prendiam o lote para sempre)
    repetir = (datetime.now(UTC) - timedelta(minutes=90)).isoformat()
    removidos = 0
    sem_resposta = {}
    with db:
        for (origem, id_, _), existe in zip(linhas, resultados):
            if existe is None:  # não deu para saber (site em baixo ou a recusar)
                db.execute("UPDATE anuncios SET verificado_em = ? WHERE origem = ? AND id = ?", (repetir, origem, id_))
                sem_resposta[origem] = sem_resposta.get(origem, 0) + 1
            else:
                db.execute("UPDATE anuncios SET removido = ?, verificado_em = ? WHERE origem = ? AND id = ?",
                           (int(not existe), agora, origem, id_))
                removidos += not existe
    for origem, falhas in sem_resposta.items():
        registo.ciclo("verificar", erro=f"{NOMES[origem]}: {falhas} anúncios sem resposta")
    return removidos


def completar_em_falta(db, config, lote=4, pausa=6.0):
    """Lê a página dos anúncios que a lista deixou sem km certos (CustoJusto) ou sem km e ano (Auto.pt).

    Os mais recentes primeiro, devagar para não ser bloqueado. Cada um é tentado uma vez; se a leitura falhar,
    volta a ser tentado passadas 6 horas. Devolve quantos ficaram com km.
    """
    repetir = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    linhas = db.execute(
        "SELECT origem, id, url, titulo, preco, ano, km, marca, modelo_site FROM anuncios "
        "WHERE origem IN ('custojusto', 'autopt') AND km_certo IS NULL AND (removido IS NULL OR removido = 0) "
        "AND preco BETWEEN ? AND ? AND (detalhe_em IS NULL OR detalhe_em < ?) "
        "ORDER BY detalhe_em IS NOT NULL, visto_em DESC LIMIT ?",
        (config["preco_min"], config["preco_max"], repetir, lote)).fetchall()
    feitos = 0
    for origem, id_, url, titulo, preco, ano, km, marca, modelo_site in linhas:
        anuncio = carro(origem, id_, titulo, preco, url, ano=ano, km=None, marca=marca, modelo_site=modelo_site)
        erro = None
        try:
            COMPLETAR[origem](anuncio)
        except (RuntimeError, StopIteration, KeyError, ValueError, TypeError) as falha:
            erro = f"{type(falha).__name__}: {falha}"
        novo_km = km_plausivel(anuncio["km"], anuncio["ano"])
        with db:
            db.execute("UPDATE anuncios SET detalhe_em = ?, km = coalesce(?, km), ano = coalesce(ano, ?), "
                       "km_certo = ? WHERE origem = ? AND id = ?",
                       (datetime.now(UTC).isoformat(), novo_km, anuncio["ano"], None if erro else 1, origem, id_))
        registo.ciclo("detalhes", lidos=1, erro=erro and f"{NOMES[origem]}: {erro}")
        feitos += novo_km is not None
        if erro and "HTTP 429" in erro:
            break  # o site pediu para abrandar: volta no próximo minuto
        time.sleep(pausa)
    return feitos




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
    elif origem in ("verificar", "detalhes"):
        intervalo = 60
    else:
        intervalo = config["intervalo_segundos"]
    falhas, ciclos, limpeza, reidentificado = 0, 0, time.monotonic(), time.monotonic()
    while not parar.is_set():
        inicio = time.monotonic()
        total = 0
        try:
            if origem == "verificar":
                removidos = verificar(db, config)
                if removidos:
                    print(f"{datetime.now():%H:%M:%S} Verificação: {removidos} anúncios eliminados dos sites "
                          "(deixam de aparecer no painel)", flush=True)
            elif origem == "detalhes":
                completar_em_falta(db, config)
            elif origem == "mercado":
                explorar(db, config, alertar_recentes=not so_guardar)
                if time.monotonic() - limpeza > 6 * 3600:  # regras da base também numa vigia longa (painel)
                    limpar(db, config)
                    limpeza = time.monotonic()
                elif time.monotonic() - reidentificado > 600:  # o catálogo cresce: dá modelo a quem não tinha
                    reidentificar(db)
                    reidentificado = time.monotonic()
            else:
                total, referencia = (ciclo_facebook(db, config, so_guardar) if origem == "facebook"
                                     else ciclo_paginado(db, origem, config, so_guardar))
                if not so_guardar and (ciclos == 0 or referencia or falhas or origem == "facebook"):  # Facebook: uma linha por volta
                    print(f"{datetime.now():%H:%M:%S} {NOMES[origem]}: {total} anúncios lidos"
                          + (" (referência inicial)" if referencia else ""), flush=True)
            falhas = 0
            erros.pop(origem, None)
            if origem != "detalhes":  # os detalhes contam-se um a um
                registo.ciclo(origem, lidos=total)
        except SessaoFacebook as error:
            # sessão morta: insistir só arrisca a conta. Avisa uma vez e pára o Facebook até à próxima execução.
            erros[origem] = str(error)
            registo.ciclo(origem, erro=error)
            registo.evento(db, "erro", origem, f"{error}. Parado até à próxima execução.")
            if not so_guardar:
                try:
                    send("Alerta Carros: o Facebook pediu login. Renova o secret FACEBOOK_COOKIES "
                         "(c_user, xs, datr) no GitHub.")
                except RuntimeError:
                    pass
            parar.wait()  # a thread fica à espera do fim (não é um erro a reiniciar)
        except Exception as error:  # noqa: BLE001 - nenhum erro pode calar um site: regista e volta a tentar
            falhas += 1
            erros[origem] = str(error)
            registo.ciclo(origem, erro=f"{type(error).__name__}: {error}")
            esperado = isinstance(error, (RuntimeError, sqlite3.Error))
            registo.evento(db, "erro", origem,
                           str(error) if esperado else traceback.format_exc(limit=8))
        ciclos += 1
        # com erros (bloqueio, site em baixo) espera cada vez mais, até 2 min, sem nunca ficar abaixo do intervalo
        espera = max(intervalo, min(120, intervalo * 2 ** min(falhas, 6))) if falhas else intervalo
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
    for _ in range(2):  # falhar a publicar nunca pára a vigia: tenta já outra vez e depois no próximo período
        try:
            resultado = subprocess.run(["gh", "release", "upload", "dados", str(copia), "--clobber"],
                                       capture_output=True, text=True, timeout=300)
            erro = resultado.stderr.strip()[:200] if resultado.returncode else None
        except (OSError, subprocess.TimeoutExpired) as error:
            erro = str(error)
        if not erro:
            break
    print(f"{datetime.now():%H:%M:%S} " + (f"Falhou a publicar a base: {erro}" if erro else "Base publicada"),
          flush=True)
    return erro


def vigiar_tudo(caminho, config, minutos=None):
    """Vigia todas as fontes em paralelo. minutos: pára ao fim desse tempo (GitHub Actions) e fecha a base.

    De minuto a minuto: reinicia threads mortas; publica a base; de 5 em 5 min resume a saúde e envia alarmes.
    """
    fim = time.monotonic() + minutos * 60 if minutos else None
    parar = threading.Event()

    def lancar(origem):
        thread = threading.Thread(target=vigiar, args=(origem, caminho, config, parar), daemon=True, name=origem)
        thread.start()
        return thread

    threads = {origem: lancar(origem) for origem in [*config["fontes"], "mercado", "verificar", "detalhes"]}
    print(f"A vigiar {', '.join(NOMES[o] for o in config['fontes'])} em paralelo "
          f"(de {config['intervalo_segundos']} em {config['intervalo_segundos']} s; "
          f"Facebook de {config['facebook_cada_minutos']} em {config['facebook_cada_minutos']} min).", flush=True)
    db = abrir_db(caminho)
    avisados, publicacoes_falhadas, tique = set(), 0, 0
    try:
        while fim is None or time.monotonic() < fim:
            parar.wait(60 if fim is None else min(60, max(1, fim - time.monotonic())))
            tique += 1
            try:  # a manutenção nunca pode deitar abaixo a vigia
                for origem, thread in list(threads.items()):
                    if not thread.is_alive():
                        registo.evento(db, "erro", origem, "a leitura parou sozinha: reiniciada")
                        threads[origem] = lancar(origem)
                if tique % 30 == 0:  # max_alertas_todos vale por cada 30 min
                    with ALERTA:
                        ENVIADOS_TODOS[0] = 0
                if tique % 5 == 0:
                    registo.resumir(db)
                    mal = registo.alarmes(config)
                    if publicacoes_falhadas >= 3:
                        mal["publicar"] = "A base não é publicada há várias tentativas: o painel está desatualizado."
                    avisar(db, config, mal, avisados)
                    if config["resumo_diario"] and not config.get("so_guardar"):
                        try:
                            resumo_diario(db, config)
                        except RuntimeError as error:  # Telegram em baixo: tenta no próximo período
                            registo.evento(db, "erro", "telegram", f"resumo diário não enviado: {error}")
                if os.environ.get("PUBLICAR_BASE") and tique % config["publicar_cada_minutos"] == 0:
                    publicacoes_falhadas = publicacoes_falhadas + 1 if publicar(caminho) else 0
            except Exception:  # noqa: BLE001
                registo.evento(db, "erro", "vigia", traceback.format_exc(limit=8))
    finally:
        parar.set()
        for thread in threads.values():
            thread.join(timeout=180)  # cada fonte acaba o ciclo em curso
        registo.resumir(db)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # tudo no .sqlite3: é esse o ficheiro guardado na cache
        db.close()
        if os.environ.get("PUBLICAR_BASE"):
            publicar(caminho)
        print(f"{datetime.now():%H:%M:%S} Fim da vigia.", flush=True)


def quase_bons(db, config, horas=24, quantos=5):
    """Carros das marcas em foco vistos nas últimas horas que não deram alerta, pela margem estimada."""
    from .mercado import analisar
    from .negocio import dentro_dos_limites, excluido
    desde = (datetime.now(UTC) - timedelta(hours=horas)).isoformat()
    foco = [slug(m) for m in config["marcas_foco"]]
    colunas = ["origem", "id", "titulo", "preco", "url", "ano", "km", "modelo", "combustivel", "caixa",
               "cilindrada", "potencia", "marca", "sinais", "extras", "importado"]
    linhas = db.execute(
        f"SELECT {', '.join(colunas)} FROM anuncios WHERE visto_em >= ? AND alertado = 0 "
        f"AND (removido IS NULL OR removido = 0) AND preco BETWEEN ? AND ? "
        f"AND marca IN ({','.join('?' * len(foco))})",
        (desde, config["preco_min"], config["preco_max"], *foco)).fetchall()
    resultado = []
    for linha in linhas:
        a = carro(**dict(zip(colunas, linha)))
        a["sinais"], a["extras"] = av.carregar(a["sinais"]) or None, av.carregar(a["extras"])
        if not a["modelo"] or excluido(a, config) or not dentro_dos_limites(a, config):
            continue
        analise = analisar(db, a, config)
        if analise["margem"] is None and a["ano"]:  # poucos carros iguais: comparação larga (só no resumo)
            analise = analisar(db, a, config, largo=True)
        if analise["margem"] is not None and analise["margem"] > 0 and not analise["irreal"]:
            resultado.append((analise["margem"], a, analise))
    resultado.sort(key=lambda x: -x[0])
    unicos = {}  # o mesmo carro publicado em vários sites aparece uma vez
    for margem, a, analise in resultado:
        unicos.setdefault((a["modelo"], a["ano"], a["km"] or a["id"], a["preco"]), (margem, a, analise))
    return list(unicos.values())[:quantos]


def resumo_diario(db, config):
    """Às config["resumo_hora"] (hora de Lisboa), uma vez por dia: os 5 "quase bons" das marcas em foco."""
    from zoneinfo import ZoneInfo
    agora = datetime.now(ZoneInfo("Europe/Lisbon"))
    hoje = int(agora.strftime("%Y%m%d"))
    db.execute("CREATE TABLE IF NOT EXISTS estado (chave TEXT PRIMARY KEY, valor INTEGER)")
    feito = db.execute("SELECT valor FROM estado WHERE chave = 'resumo_diario'").fetchone()
    if agora.hour < config["resumo_hora"] or (feito and feito[0] >= hoje):
        return False
    linhas = ["Resumo de ontem: os quase bons negócios "
              + ", ".join(m.title() for m in config["marcas_foco"]) + " (não chegaram ao critério do alerta)"]
    for i, (margem, a, analise) in enumerate(quase_bons(db, config), 1):
        factos = [euros(a["preco"]), str(a["ano"] or "ano?"),
                  f"{a['km']:,} km".replace(",", " ") if a["km"] else "sem km", a["combustivel"], a["caixa"]]
        linhas.append(f"\n{i}. {a['titulo'][:60]}\n" + " · ".join(f for f in factos if f)
                      + f"\nMercado ~{euros(analise['mediana'])} ({analise['desconto']}% abaixo, "
                      f"{analise['n']} comparáveis{', comparação fraca' if analise.get('largo') else ''}) "
                      f"· margem +{euros(margem)}"
                      + (f"\n⚠️ AVARIA: {', '.join(analise['avaria']['itens'][:3])}" if analise["avaria"]["tem"] else "")
                      + f"\n{a['url']}")
    if len(linhas) == 1:
        linhas.append("\nNenhum carro com margem positiva nas últimas 24 h.")
    send("\n".join(linhas)[:4000])
    with db:
        db.execute("INSERT OR REPLACE INTO estado VALUES ('resumo_diario', ?)", (hoje,))
    return True


def avisar(db, config, mal, avisados):
    """Alarme no Telegram uma vez por problema; volta a poder avisar depois de recuperar."""
    for chave, texto in mal.items():
        if chave in avisados:
            continue
        registo.evento(db, "alarme", chave.split(":")[-1], texto)
        if config["alarmes_telegram"]:
            try:
                send(f"⚠️ Alerta Carros: {texto}")
            except RuntimeError as error:
                registo.evento(db, "erro", "telegram", f"alarme não enviado: {error}")
                continue
        avisados.add(chave)
    avisados.intersection_update(mal)
