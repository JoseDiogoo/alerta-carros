"""Valor de mercado: carros iguais, preços ajustados a ano e km, mediana prudente."""

import math
import statistics
import time

from . import avaliacao as av


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


_CACHE_DEPRECIACAO = {}  # (base, modelo, ano, combustível) -> (quando, coeficientes)
CACHE_SEGUNDOS = 600  # a desvalorização de um modelo muda devagar: recalcular de 10 em 10 min chega


def depreciacao(db, anuncio):
    """Quanto este modelo perde por ano e por 10 000 km, aprendido dos anúncios (log-preço ~ ano + km).

    Fica em cache por base de dados: o painel avalia milhares de carros do mesmo modelo de 15 em 15 s.
    """
    base = db.execute("PRAGMA database_list").fetchone()[2]  # "" = base em memória (testes): sem cache
    chave = (base, anuncio["modelo"], anuncio["ano"], anuncio.get("combustivel"))
    guardado = _CACHE_DEPRECIACAO.get(chave) if base else None
    if guardado and time.monotonic() - guardado[0] < CACHE_SEGUNDOS:
        return guardado[1]
    if len(_CACHE_DEPRECIACAO) > 20000:
        _CACHE_DEPRECIACAO.clear()
    coef = _depreciacao(db, anuncio)
    if base:
        _CACHE_DEPRECIACAO[chave] = (time.monotonic(), coef)
    return coef


def _depreciacao(db, anuncio):
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


def comparar(db, anuncio, minimo=1, largo=False):
    """Valor de mercado rigoroso.

    1. Só carros do mesmo modelo, combustível, ano ±2 e km parecidos.
    2. Procura primeiro os de motor (cilindrada, potência) e caixa iguais; só alarga se forem poucos.
    3. Ajusta o preço de cada um à diferença de ano e de km (desvalorização aprendida por modelo).
    4. Tira preços absurdos (peças, erros) e usa a mediana.

    largo=True (só o painel, nunca o Telegram): km ±80 % e aceita comparáveis sem km (sem ajuste de km).
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
        margem_km = max(50000, anuncio["km"] * 0.8) if largo else max(35000, anuncio["km"] * 0.45)
        sql += " AND (km IS NULL OR km BETWEEN ? AND ?)" if largo else " AND km BETWEEN ? AND ?"
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


def analisar(db, anuncio, config, largo=False):
    """Valor de mercado, venda provável, margem e pontuação (sem aplicar os mínimos do utilizador).

    largo=True: comparação alargada (ver comparar); o resultado é sempre prudente e marcado "largo"."""
    info = comparar(db, anuncio, config["min_comparaveis"], largo)
    sinais = anuncio.get("sinais") or av.sinais_do_texto(anuncio["titulo"], anuncio["texto"])
    if info["mediana"] is None:
        return {**info, "venda": None, "margem": None, "desconto": None, "ratio": None, "suspeito": False,
                "fiavel": False, "prudente_usado": False, "irreal": False, "avaria": av.avaria(sinais),
                "pontuacao": None, "fortes": [], "alertas": [], "sinais": sinais}
    # Comparação fraca (sem km, só o modelo, preços muito variáveis): usa um valor prudente (percentil 30)
    # em vez da mediana, para a margem nunca ser inflacionada por falta de dados.
    fraca = (largo or not anuncio["km"] or info["nivel"] == "mesmo modelo e combustível"
             or (info["dispersao"] or 0) > 0.35)
    mediana = info["prudente"] if fraca else info["mediana"]
    venda = mediana * config["fator_venda"]  # os anúncios são preços pedidos: vende-se um pouco abaixo
    # avarias: só etiqueta, sem custo (a gravidade é desconhecida); a margem é a do carro como se estivesse bom
    analise = {**info, "largo": largo, "mediana": round(mediana), "prudente_usado": fraca, "venda": round(venda),
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
