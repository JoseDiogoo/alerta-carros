"""Configuração (config.toml) e segredos."""

import os
import tomllib
from pathlib import Path

from .texto import words
from .anuncio import NOMES

ROOT = Path(__file__).resolve().parent.parent


def secret(name):
    value = os.environ.get(name, "").strip()
    segredos = ROOT / ".streamlit" / "secrets.toml"
    if not value and segredos.is_file():
        with segredos.open("rb") as file:
            value = str(tomllib.load(file).get(name, "")).strip()
    if not value:
        raise ValueError(f"Define {name} em .streamlit/secrets.toml ou no PowerShell.")
    return value


PADROES = {
    "intervalo_segundos": 15, "paginas_max": 5, "paginas_semente": 15, "paginas_exploracao": 3,
    "preco_min": 300, "preco_max": 15000, "ano_min": 1960, "km_max": 250000, "km_ano_max": 20000,
    "desconto_telegram": 0.30, "desconto_foco": 0.15, "comparaveis_telegram": 10,
    "limite_burla": 0.5, "limite_irreal": 0.35, "min_comparaveis": 6, "descida_minima": 0.05,
    "custos_revenda": 500, "fator_venda": 0.95, "retencao_dias": 60, "preco_max_guardar": 15000,
    "marcas_foco": ["toyota"], "so_foco_telegram": True, "pontuacao_minima": 75, "alertar_todos": False, "max_alertas_todos": 40,
    "apenas_nacionais": False, "modelos": [], "excluir": [], "excluir_texto": [],
    "fontes": ["standvirtual", "olx", "custojusto", "autopt", "autox", "facebook"],
    "facebook_cidades": ["lisbon", "porto", "braga", "coimbra", "faro", "viseu", "evora"],
    "facebook_cada_minutos": 3, "facebook_faixas": 3,
    "alerta_exploracao_minutos": 180, "feed_com_teto": [], "publicar_cada_minutos": 2,
    "alarmes_telegram": True, "alarme_sem_novos_minutos": 180, "margem_minima_telegram": 800,
}


def load_config(path):
    with path.open("rb") as file:
        config = {**PADROES, **tomllib.load(file)}
    if any(source not in NOMES for source in config["fontes"]):
        raise ValueError(f"Fontes válidas: {', '.join(NOMES)}.")
    for key in ("intervalo_segundos", "paginas_max", "paginas_semente", "paginas_exploracao", "min_comparaveis",
                "comparaveis_telegram", "facebook_cada_minutos", "facebook_faixas", "retencao_dias",
                "preco_max_guardar", "alerta_exploracao_minutos", "publicar_cada_minutos",
                "alarme_sem_novos_minutos"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} tem de ser um número inteiro positivo.")
    if config["intervalo_segundos"] < 10:
        raise ValueError("Usa um intervalo de pelo menos 10 segundos para não seres bloqueado.")
    for key in ("desconto_telegram", "desconto_foco", "limite_burla", "limite_irreal", "descida_minima", "fator_venda"):
        if not 0 < config[key] <= 1:
            raise ValueError(f"{key} tem de estar entre 0 e 1 (ex.: 0.85).")
    if not 0 < config["preco_min"] < config["preco_max"]:
        raise ValueError("preco_min tem de ser menor do que preco_max.")
    if any(not words(term) for term in config["excluir"] + config["excluir_texto"] + config["modelos"]):
        raise ValueError("As listas de modelos/exclusões contêm um termo vazio.")
    return config
