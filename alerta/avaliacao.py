"""Avaliação detalhada de cada carro: descrição, características e mercado -> pontuação 0-100.

Funciona com qualquer marca/modelo: só usa texto do anúncio, campos estruturados dos sites
e a comparação com carros iguais. Sem listas de modelos.
"""

import json
import re
import unicodedata


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", texto))


# Problemas graves: o carro pode não valer o preço de mercado (avaria cara, legal, segurança).
GRAVES = [
    "motor partido", "motor avariado", "motor gripado", "motor a precisar", "precisa de motor", "motor com problema",
    "problema no motor", "problema de motor", "problemas no motor", "falha de motor", "motor nao pega",
    "caixa avariada", "caixa partida", "problema na caixa", "problema de caixa", "problemas na caixa",
    "caixa a precisar", "caixa com problema", "caixa faz barulho", "caixa de velocidades avariada",
    "junta da cabeca", "junta de cabeca", "cabeca do motor", "deita fumo", "fuma muito", "fumo branco", "fumo azul",
    "consome oleo", "gasta oleo", "come oleo", "turbo avariado", "turbo partido", "turbo a precisar",
    "injetores avariados", "bomba injetora", "embraiagem partida", "volante bimassa", "bimassa",
    "airbag disparado", "airbags disparados", "airbag aberto", "luz do airbag", "luz airbag",
    "penhora", "penhorado", "penhorada", "reserva de propriedade", "com reserva", "sem documentos", "sem livrete",
    "sem dua", "documentos em atraso", "sem matricula", "matricula estrangeira", "nao legalizado", "por legalizar",
    "inspecao chumbada", "chumbou na inspecao", "sem inspecao", "inspecao caducada",
    "nao pega", "nao anda", "nao trabalha", "nao arranca", "parado ha", "esta parado",
    "salvado", "sinistrado", "sinistro", "acidentado", "batido", "danificado", "danos", "estragos", "capotado",
    "inundado", "inundacao", "submerso", "chassis empenado", "chassi torto", "chassis torto",
    "para pecas", "vende se para pecas", "pecas", "para reparar", "reparar", "avariado", "avaria",
    "para reparacao", "precisa de reparacao", "a precisar de reparacao", "com avaria", "com defeito",
    "nao liga", "motor fundido", "caixa estragada", "motor estragado",
    "luz do motor", "luz de avaria", "check engine", "modo de seguranca", "fap entupido", "filtro de particulas entupido",
    "egr avariada", "corrente de distribuicao", "barulho no motor", "motor a bater", "bate biela",
    "sem chave", "chave perdida", "leilao", "sucata", "abate",
]

# Problemas menores: baixam o valor mas são reparáveis.
LEVES = [
    "precisa de", "necessita de", "a precisar", "pequeno toque", "toque", "riscos", "risco", "amolgadela",
    "amolgadelas", "mossa", "mossas", "ferrugem", "pintura gasta", "verniz", "embraiagem a patinar",
    "embraiagem gasta", "pneus gastos", "travoes gastos", "pastilhas", "ar condicionado nao", "ac nao funciona",
    "ar condicionado avariado", "barulho", "ruido", "vibra", "vibracao", "folga", "suspensao", "amortecedores",
    "vidro partido", "para brisas partido", "farol partido", "espelho partido", "bateria fraca", "bateria morta",
    "fecho centralizado nao", "vidros eletricos nao", "luz acesa", "sensor avariado", "precisa de revisao",
    "revisao por fazer", "iuc em atraso", "km nao confirmados", "conta quilometros", "quilometros nao",
    "importado", "vende se no estado", "no estado em que se encontra", "sem garantia",
]

# Pontos fortes: aumentam a confiança no negócio.
BONS = [
    "unico dono", "1 dono", "um dono", "primeiro dono", "dono unico", "livro de revisoes", "livro revisoes",
    "revisoes em dia", "revisoes feitas", "revisoes na marca", "revisoes na marca", "historico de revisoes",
    "historico de manutencao", "manutencao em dia", "manutencao na marca", "faturas", "todas as faturas",
    "distribuicao feita", "distribuicao nova", "correia de distribuicao nova", "kit de distribuicao",
    "embraiagem nova", "embraiagem feita", "pneus novos", "travoes novos", "pastilhas novas", "discos novos",
    "bateria nova", "oleo mudado", "revisao feita", "revisao recente", "inspecao valida", "inspecao em dia",
    "inspecao feita", "inspecao ate", "iuc pago", "nao fumador", "sempre em garagem", "garagem", "garantia",
    "como novo", "impecavel", "excelente estado", "muito bom estado", "otimo estado", "estado impecavel",
    "nacional", "segunda chave", "2 chaves", "duas chaves", "chave suplente", "km reais", "km certificados",
    "quilometros certificados", "certificado", "sem acidentes", "nunca batido", "sem toques", "pintura original",
    "relatorio", "carfax", "pouco uso", "pouco usado", "senhora", "reformado", "todos os extras",
]

NEGACOES = {"sem", "nunca", "nao", "nenhum", "nenhuma", "zero", "livre"}

# Avarias mecânicas/elétricas e danos: o carro aparece na mesma, com a etiqueta "AVARIA". Não se estima o custo
# (só um mecânico sabe) e não tiram pontos: a decisão é de quem compra.
AVARIAS = {
    "avariado", "avaria", "nao pega", "nao anda", "nao trabalha", "nao arranca", "parado ha", "esta parado",
    "nao liga", "com avaria", "com defeito", "para reparar", "reparar", "para reparacao",
    "precisa de reparacao", "a precisar de reparacao",
    "motor partido", "motor avariado", "motor gripado", "motor a precisar", "precisa de motor",
    "motor com problema", "problema no motor", "problema de motor", "problemas no motor", "falha de motor",
    "motor nao pega", "motor fundido", "motor estragado", "barulho no motor", "motor a bater", "bate biela",
    "junta da cabeca", "junta de cabeca", "cabeca do motor", "caixa avariada", "caixa partida",
    "problema na caixa", "problema de caixa", "problemas na caixa", "caixa a precisar", "caixa com problema",
    "caixa faz barulho", "caixa de velocidades avariada", "caixa estragada", "sinistrado", "sinistro",
    "acidentado", "batido", "danificado", "danos", "estragos",
    "deita fumo", "fuma muito", "fumo branco", "fumo azul", "consome oleo", "gasta oleo", "come oleo",
    "turbo avariado", "turbo partido", "turbo a precisar", "volante bimassa", "bimassa", "corrente de distribuicao",
    "injetores avariados", "bomba injetora", "airbag disparado", "airbags disparados", "airbag aberto",
    "luz do airbag", "luz airbag", "embraiagem partida", "embraiagem a patinar", "embraiagem gasta",
    "fap entupido", "filtro de particulas entupido", "luz do motor", "luz de avaria", "check engine",
    "modo de seguranca", "egr avariada", "ar condicionado avariado", "ar condicionado nao", "ac nao funciona",
    "sensor avariado",
}
# Avaria sem nome: só aparece na etiqueta quando a descrição não diz o que é.
GENERICOS = {"avariado", "avaria", "nao pega", "nao anda", "nao trabalha", "nao arranca", "parado ha", "esta parado",
             "nao liga", "com avaria", "com defeito", "para reparar", "reparar", "para reparacao",
             "precisa de reparacao", "a precisar de reparacao"}
# Peças, problemas legais ou estruturais: não é negócio (nunca vai para o Telegram).
INVIAVEIS = {
    "penhora", "penhorado", "penhorada", "reserva de propriedade", "com reserva", "sem documentos", "sem livrete",
    "sem dua", "documentos em atraso", "sem matricula", "matricula estrangeira", "nao legalizado", "por legalizar",
    "salvado", "capotado", "inundado", "inundacao", "submerso", "chassis empenado", "chassi torto", "chassis torto",
    "para pecas", "vende se para pecas", "pecas", "leilao", "sucata", "abate",
}


def avaria(sinais):
    """O que a descrição diz que está avariado (sem custo) e se há motivo para não comprar.

    Devolve itens (para a etiqueta), tem (há avaria), incerta (avaria sem nome) e inviaveis.
    """
    termos = [*sinais.get("graves", []), *sinais.get("leves", [])]
    concretos = [t for t in termos if t in AVARIAS and t not in GENERICOS]
    genericos = [t for t in termos if t in GENERICOS]
    itens = concretos or genericos[:1]
    return {"itens": itens, "tem": bool(itens), "incerta": not concretos and bool(genericos),
            "inviaveis": [t for t in sinais.get("graves", []) if t in INVIAVEIS]}


def _encontra(texto, termos, ignorar_negados):
    achados = []
    for termo in termos:
        for m in re.finditer(rf"(?:^| ){re.escape(termo)}(?= |$)", texto):
            anteriores = texto[:m.start()].split()
            if "|" in anteriores:  # a negação só vale dentro da mesma frase
                anteriores = anteriores[len(anteriores) - anteriores[::-1].index("|"):]
            if ignorar_negados and NEGACOES & set(anteriores[-3:]):
                continue  # "sem danos", "nunca batido", "não tem avarias"
            achados.append(termo)
            break
    # tira termos contidos noutros já encontrados ("avaria" dentro de "luz de avaria")
    return [t for t in achados if not any(t != o and f" {t} " in f" {o} " for o in achados)]


def sinais_do_texto(titulo, descricao):
    # frases separadas por " | ": um termo não atravessa frases e "não" só nega a própria frase
    frases = re.split(r"[.,;:!?\n]+", f"{titulo}. {descricao}")
    texto = " | ".join(filter(None, (normalizar(f) for f in frases)))
    return {"graves": _encontra(texto, GRAVES, True), "leves": _encontra(texto, LEVES, True),
            "bons": _encontra(texto, BONS, False), "tem_descricao": len(normalizar(descricao)) > 40}


def pontuar(anuncio, mercado, sinais, extras):
    """Pontuação 0-100 e motivos. mercado: dict com mediana, n, desconto, margem, km_mediano."""
    pontos, fortes, alertas = 50.0, [], []
    if mercado.get("desconto") is not None:
        pontos += max(-25, min(30, mercado["desconto"] * 0.9))
        pontos += max(-10, min(10, mercado["margem"] / 600))
        if mercado["desconto"] >= 50:
            pontos -= 20
            alertas.append("preço muito abaixo do mercado (possível burla)")
        confianca = min(1.0, mercado["n"] / 15)
        if (mercado.get("dispersao") or 0) > 0.35:  # comparáveis com preços muito diferentes entre si
            confianca *= 0.5
            alertas.append("preços de mercado muito variáveis para este carro")
        if mercado.get("nivel") == "mesmo motor e caixa":
            fortes.append("comparado com carros de motor e caixa iguais")
        elif mercado.get("nivel") == "mesmo modelo e combustível":
            confianca *= 0.8
        pontos = 50 + (pontos - 50) * (0.6 + 0.4 * confianca)  # poucos comparáveis = menos certeza
    else:
        alertas.append("sem carros comparáveis suficientes")
    km, kmmed = anuncio.get("km"), mercado.get("km_mediano")
    if km and kmmed:
        rel = km / kmmed
        if rel <= 0.75:
            pontos += 6
            fortes.append(f"km abaixo da média ({int(kmmed):,} km)".replace(",", " "))
        elif rel >= 1.3:
            pontos -= 6
            alertas.append(f"km acima da média ({int(kmmed):,} km)".replace(",", " "))
    # avarias não tiram pontos (a gravidade é desconhecida): aparecem à parte, na etiqueta AVARIA
    for termo in sinais.get("graves", []):
        if termo in INVIAVEIS:
            pontos -= 30
            alertas.append(f"menciona «{termo}»")
        elif termo not in AVARIAS:  # inspeção, chave perdida...
            pontos -= 8
            alertas.append(f"menciona «{termo}»")
    graves = [f" {g} " for g in sinais.get("graves", [])]
    leves = [t for t in sinais.get("leves", [])  # "precisa de" já está dentro de "precisa de motor"
             if t not in AVARIAS and not any(f" {t} " in g for g in graves)]
    for termo in leves[:4]:
        pontos -= 3
        alertas.append(f"menciona «{termo}»")
    for termo in sinais.get("bons", [])[:6]:
        pontos += 3
        fortes.append(termo)
    if not sinais.get("tem_descricao"):
        pontos -= 3
        alertas.append("sem descrição")
    extras = extras or {}
    if extras.get("garantia"):
        pontos += 6
        fortes.append(f"garantia {extras['garantia']}")
    if extras.get("donos") == 1:
        pontos += 5
        fortes.append("1 dono")
    elif (extras.get("donos") or 0) >= 4:
        pontos -= 4
        alertas.append(f"{extras['donos']} donos")
    if extras.get("segunda_chave"):
        pontos += 2
        fortes.append("2.ª chave")
    if extras.get("nao_fumador"):
        pontos += 2
        fortes.append("não fumador")
    if extras.get("equipamento", 0) >= 25:
        pontos += 3
        fortes.append(f"{extras['equipamento']} equipamentos")
    if anuncio.get("importado"):
        pontos -= 3
        alertas.append("importado")
    elif anuncio.get("importado") is False:
        pontos += 2
        fortes.append("nacional")
    if anuncio.get("profissional"):
        pontos += 2
        fortes.append("stand (obrigado a dar garantia)")
    return max(0, min(100, round(pontos))), list(dict.fromkeys(fortes)), list(dict.fromkeys(alertas))


def extras_standvirtual(advert):
    """Campos estruturados da página de anúncio do Standvirtual."""
    p = {k: ((v.get("values") or [{}])[0].get("label") if isinstance(v, dict) else v)
         for k, v in (advert.get("parametersDict") or {}).items()}
    donos = re.search(r"\d+", str(p.get("number_of_registrations") or ""))
    return {"garantia": p.get("vendors_warranty_valid_until_date"),
            "donos": int(donos.group()) if donos else None,
            "segunda_chave": str(p.get("second_key", "")).startswith("Sim"),
            "nao_fumador": str(p.get("non_smoking", "")).startswith("Sim"),
            "equipamento": sum(1 for v in p.values() if str(v).startswith("Sim")),
            "importado": {"Sim": True, "Não": False}.get(p.get("is_imported_car"))}


def carregar(valor):
    try:
        return json.loads(valor) if valor else {}
    except ValueError:
        return {}


if __name__ == "__main__":
    s = sinais_do_texto("VW Golf 1.6 TDI", "Único dono, sem danos, nunca batido, livro de revisões. Precisa de pneus.")
    assert "danos" not in s["graves"] and "batido" not in s["graves"], s
    assert "unico dono" in s["bons"] and "livro de revisoes" in s["bons"] and "precisa de" in s["leves"], s
    s2 = sinais_do_texto("Clio", "Motor gripado, vende-se para peças.")
    assert "motor gripado" in s2["graves"] and any("pecas" in t for t in s2["graves"]), s2
    bom = pontuar({"km": 90000, "profissional": False, "importado": False},
                  {"desconto": 25, "margem": 2000, "n": 15, "km_mediano": 150000}, s, {"donos": 1})
    mau = pontuar({"km": 90000}, {"desconto": 25, "margem": 2000, "n": 15, "km_mediano": 150000}, s2, {})
    assert bom[0] > 75 > mau[0], (bom, mau)
    print("ok", bom[0], mau[0])
