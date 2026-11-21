"""Base de dados SQLite: esquema, gravação e regras de limpeza."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from . import avaliacao as av
from .texto import modelo_de


def abrir_db(path, **opcoes):
    db = sqlite3.connect(path, timeout=30, **opcoes)
    db.execute("PRAGMA journal_mode = WAL")  # várias threads a ler e escrever ao mesmo tempo
    db.execute("CREATE TABLE IF NOT EXISTS anuncios (origem TEXT, id TEXT, preco INTEGER, ano INTEGER, "
               "km INTEGER, alertado INTEGER DEFAULT 0, visto_em TEXT, titulo TEXT, url TEXT, local TEXT, "
               "PRIMARY KEY (origem, id))")
    colunas = {row[1] for row in db.execute("PRAGMA table_info(anuncios)")}
    for coluna in ("modelo", "foto", "combustivel", "caixa", "profissional", "importado", "sinais", "extras",
                   "cilindrada", "potencia", "carrocaria", "atualizado_em"):
        if coluna not in colunas:
            db.execute(f"ALTER TABLE anuncios ADD COLUMN {coluna}")
    if "atualizado_em" not in colunas:
        db.execute("UPDATE anuncios SET atualizado_em = visto_em")
    db.execute("CREATE INDEX IF NOT EXISTS anuncios_modelo ON anuncios (modelo, ano)")  # comparáveis
    db.execute("CREATE INDEX IF NOT EXISTS anuncios_visto ON anuncios (visto_em)")  # mais recentes (painel)
    db.execute("CREATE INDEX IF NOT EXISTS anuncios_atualizado ON anuncios (atualizado_em)")  # limpeza
    versao = db.execute("PRAGMA user_version").fetchone()[0]
    antigos = db.execute("SELECT origem, id, titulo FROM anuncios WHERE titulo IS NOT NULL AND "
                         "(modelo IS NULL OR ? < 5)", (versao,)).fetchall()
    db.execute("PRAGMA user_version = 5")  # subir quando modelo_de muda: recalcula os modelos guardados
    if antigos:
        with db:
            db.executemany("UPDATE anuncios SET modelo = ? WHERE origem = ? AND id = ?",
                           [(modelo_de(t), o, i) for o, i, t in antigos])
    return db


def vale_guardar(anuncio, config):
    """Regra da base: só carros (marca e modelo reconhecidos) até preco_max_guardar (comparáveis úteis)."""
    return bool(anuncio.get("modelo")) and anuncio["preco"] <= config["preco_max_guardar"]


def guardar(db, anuncio, alertado):
    agora = datetime.now(UTC).isoformat()
    with db:
        db.execute(
            "INSERT INTO anuncios (origem, id, modelo, preco, ano, km, alertado, visto_em, titulo, url, local, "
            "foto, combustivel, caixa, profissional, importado, sinais, extras, cilindrada, potencia, carrocaria, "
            "atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (origem, id) DO UPDATE SET preco = excluded.preco, modelo = excluded.modelo, "
            "atualizado_em = excluded.atualizado_em, "
            "ano = coalesce(excluded.ano, ano), km = coalesce(excluded.km, km), "
            "alertado = max(alertado, excluded.alertado), foto = coalesce(excluded.foto, foto), "
            "sinais = excluded.sinais, extras = coalesce(excluded.extras, extras), "
            "combustivel = coalesce(excluded.combustivel, combustivel), caixa = coalesce(excluded.caixa, caixa), "
            "cilindrada = coalesce(excluded.cilindrada, cilindrada), potencia = coalesce(excluded.potencia, potencia), "
            "carrocaria = coalesce(excluded.carrocaria, carrocaria)",
            (anuncio["origem"], anuncio["id"], anuncio["modelo"], anuncio["preco"], anuncio["ano"], anuncio["km"],
             int(alertado), agora, anuncio["titulo"], anuncio["url"],
             anuncio["local"], anuncio["foto"], anuncio["combustivel"], anuncio["caixa"],
             anuncio["profissional"], anuncio["importado"],
             json.dumps(anuncio.get("sinais") or av.sinais_do_texto(anuncio["titulo"], anuncio["texto"])),
             json.dumps(anuncio["extras"]) if anuncio.get("extras") else None,
             anuncio.get("cilindrada"), anuncio.get("potencia"), anuncio.get("carrocaria"), agora))


def limpar(db, config):
    """Regras da base, para não crescer sem fim:

    - anúncios que não aparecem nos sites há retencao_dias (vendidos ou retirados) saem, exceto os favoritos;
    - o que não é carro ou passa de preco_max_guardar sai (não serve para comparar).
    Devolve o número de anúncios apagados.
    """
    limite = (datetime.now(UTC) - timedelta(days=config["retencao_dias"])).isoformat()
    favoritos = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'favoritos_v2'").fetchone()
    protegidos = " AND (origem, id) NOT IN (SELECT origem, id FROM favoritos_v2)" if favoritos else ""
    with db:
        apagados = db.execute(
            "DELETE FROM anuncios WHERE (atualizado_em < ? OR modelo IS NULL OR preco > ?)" + protegidos,
            (limite, config["preco_max_guardar"])).rowcount
    db.execute("PRAGMA optimize")
    if apagados > 1000:
        db.execute("VACUUM")  # devolve o espaço ao disco (e encolhe a cache do GitHub)
    return apagados


def fonte_vazia(db, origem):
    return not db.execute("SELECT 1 FROM anuncios WHERE origem = ? LIMIT 1", (origem,)).fetchone()


def duplicado(db, anuncio):
    if not (anuncio["km"] and anuncio["ano"]):
        return False
    return db.execute(
        "SELECT 1 FROM anuncios WHERE alertado = 1 AND modelo = ? AND ano = ? AND km = ? "
        "AND ABS(preco - ?) <= 250 AND origem != ?",
        (anuncio["modelo"], anuncio["ano"], anuncio["km"], anuncio["preco"], anuncio["origem"])).fetchone() is not None
