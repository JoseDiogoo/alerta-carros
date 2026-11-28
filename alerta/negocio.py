"""Regras de negócio: filtros do utilizador e critério dos alertas (Telegram)."""

from datetime import datetime

from .mercado import analisar
from .catalogo import slug
from .texto import words


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


def candidato(db, anuncio, config):
    """Passa no mercado/margem, antes de olhar à descrição? (decide se vale ler a página)."""
    if anuncio["origem"] == "autopt" and anuncio["km"] is None:
        return config["preco_min"] <= anuncio["preco"] <= config["preco_max"]
    neutro = {"graves": [], "leves": [], "bons": [], "tem_descricao": True}
    return avaliar(db, {**anuncio, "sinais": neutro}, {**config, "pontuacao_minima": 0}) is not None


def avaliar(db, anuncio, config):
    """Análise, se o carro for dos melhores negócios (vai para o Telegram); senão None."""
    if not (interessa(anuncio, config) and dentro_dos_limites(anuncio, config)):
        return None
    if apenas_foco(config) and not de_foco(anuncio, config):
        return None  # Telegram só para as marcas em foco (Toyota); as outras ficam só no painel
    analise = analisar(db, anuncio, config)
    return analise if melhor(anuncio, analise, config) else None


def porque_nao(db, anuncio, config):
    """Porque é que um anúncio novo não foi para o Telegram (para o registo de decisões)."""
    if not anuncio.get("modelo"):
        return "sem modelo certo"
    if excluido(anuncio, config):
        return "excluído por palavras"
    if apenas_foco(config) and not de_foco(anuncio, config):
        return "marca fora do foco"
    price, km, year = anuncio["preco"], anuncio["km"], anuncio["ano"]
    if not config["preco_min"] <= price <= config["preco_max"]:
        return "fora do preço"
    if year and year < config["ano_min"]:
        return "ano antigo de mais"
    if km and km > config["km_max"]:
        return "km a mais"
    if not dentro_dos_limites(anuncio, config):
        return "km por ano a mais ou importado"
    analise = analisar(db, anuncio, config)
    if analise["mediana"] is None:
        return f"poucos comparáveis ({analise['n']})"
    if not analise["fiavel"]:
        return "preços de mercado dispersos"
    if analise["irreal"]:
        return "preço irreal"
    if analise["avaria"]["inviaveis"]:
        return "peças ou problema legal"
    desconto = config["desconto_foco"] if de_foco(anuncio, config) else config["desconto_telegram"]
    if analise["ratio"] > 1 - desconto:
        return f"desconto insuficiente ({analise['desconto']}%)"
    if analise["n"] < config["comparaveis_telegram"]:
        return f"poucos comparáveis ({analise['n']})"
    if analise["pontuacao"] < config["pontuacao_minima"]:
        return f"pontuação baixa ({analise['pontuacao']})"
    return "cumpre o critério mas não foi enviado"


def apenas_foco(config):
    """O Telegram só avisa das marcas em foco? (sem marcas_foco não há filtro)"""
    return bool(config["so_foco_telegram"] and config["marcas_foco"])


def de_foco(anuncio, config):
    """Marca em foco (marcas_foco, ex.: Toyota): critério do Telegram mais solto."""
    marca = anuncio.get("marca") or (anuncio.get("modelo") or "").split(" ")[0]
    return marca in [slug(m) for m in config["marcas_foco"]]


def melhor(anuncio, analise, config):
    """Critério do Telegram: muito abaixo de um valor de mercado fiável e boa pontuação. Avariados contam."""
    if analise["mediana"] is None or not analise["fiavel"] or analise["irreal"]:  # disperso ou preço irreal
        return False
    if analise["avaria"]["inviaveis"]:
        return False  # peças, problemas legais ou estruturais: não é negócio
    desconto = config["desconto_foco"] if de_foco(anuncio, config) else config["desconto_telegram"]
    return (analise["ratio"] <= 1 - desconto and analise["n"] >= config["comparaveis_telegram"]
            and analise["pontuacao"] >= config["pontuacao_minima"])
