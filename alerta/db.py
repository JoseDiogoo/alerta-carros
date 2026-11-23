"""Base de dados SQLite: esquema, gravação e regras de limpeza."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from . import avaliacao as av
from .catalogo import catalogo, identificar


def abrir_db(path, **opcoes):
    db = sqlite3.connect(path, timeout=30, **opcoes)
    db.execute("PRAGMA journal_mode = WAL")  # várias threads a ler e escrever ao mesmo tempo
    db.execute("CREATE TABLE IF NOT EXISTS anuncios (origem TEXT, id TEXT, preco INTEGER, ano INTEGER, "
               "km INTEGER, alertado INTEGER DEFAULT 0, visto_em TEXT, titulo TEXT, url TEXT, local TEXT, "
               "PRIMARY KEY (origem, id))")
    colunas = {row[1] for row in db.execute("PRAGMA table_info(anuncios)")}
    for coluna in ("modelo", "foto", "combustivel", "caixa", "profissional", "importado", "sinais", "extras",
                   "cilindrada", "potencia", "carrocaria", "atualizado_em", "marca", "modelo_site", "modelo_fonte",
                   "removido", "verificado_em"):
        if coluna not in colunas:
            db.execute(f"ALTER TABLE anuncios ADD COLUMN {coluna}")
    if "atualizado_em" not in colunas:
        db.execute("UPDATE anuncios SET atualizado_em = visto_em")
    db.execute("CREATE INDEX IF NOT EXISTS anuncios_modelo ON anuncios (modelo, ano)")  # comparáveis
    db.execute("CREATE INDEX IF NOT EXISTS anuncios_visto ON anuncios (visto_em)")  # mais recentes (painel)
    db.execute("CREATE INDEX IF NOT EXISTS anuncios_atualizado ON anuncios (atualizado_em)")  # limpeza
    if db.execute("PRAGMA user_version").fetchone()[0] < 8:
        # versões antigas adivinhavam o modelo pelo título: esses modelos deixam de contar. Cada anúncio
        # volta a ter modelo quando for lido de novo (campos do site) ou pelo catálogo (reidentificar).
        with db:
            db.execute("UPDATE anuncios SET modelo = NULL, modelo_fonte = NULL "
                       "WHERE modelo_fonte IS NULL OR modelo_fonte != 'site'")
        db.execute("PRAGMA user_version = 8")
    return db


# fontes em que tudo é carro; o Facebook (categoria de veículos) traz também motas e acessórios
SO_CARROS = ("standvirtual", "olx", "custojusto", "autopt", "autox")


def vale_guardar(anuncio, config):
    """Regra da base: até preco_max_guardar (acima disso não serve de comparação). Um carro sem modelo certo
    guarda-se na mesma (aparece no painel); do Facebook só com modelo certo (pode não ser um carro)."""
    if anuncio["preco"] > config["preco_max_guardar"]:
        return False
    return bool(anuncio.get("modelo")) or anuncio["origem"] in SO_CARROS


def guardar(db, anuncio, alertado):
    agora = datetime.now(UTC).isoformat()
    with db:
        db.execute(
            "INSERT INTO anuncios (origem, id, modelo, preco, ano, km, alertado, visto_em, titulo, url, local, "
            "foto, combustivel, caixa, profissional, importado, sinais, extras, cilindrada, potencia, carrocaria, "
            "atualizado_em, marca, modelo_site, modelo_fonte) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (origem, id) DO UPDATE SET preco = excluded.preco, "
            # um modelo certo nunca é apagado por uma leitura sem modelo (ex.: lista sem campos)
            "modelo_fonte = CASE WHEN excluded.modelo IS NOT NULL THEN excluded.modelo_fonte ELSE modelo_fonte END, "
            "modelo = coalesce(excluded.modelo, modelo), marca = coalesce(excluded.marca, marca), "
            "modelo_site = coalesce(excluded.modelo_site, modelo_site), "
            "atualizado_em = excluded.atualizado_em, removido = 0, "
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
             anuncio.get("cilindrada"), anuncio.get("potencia"), anuncio.get("carrocaria"), agora,
             anuncio.get("marca"), anuncio.get("modelo_site"), anuncio.get("modelo_fonte")))


def limpar(db, config):
    """Regras da base, para não crescer sem fim:

    - anúncios que não aparecem nos sites há retencao_dias (vendidos ou retirados) saem, exceto os favoritos;
    - o que passa de preco_max_guardar sai (não serve para comparar);
    - os anúncios sem modelo certo voltam a ser identificados pelo catálogo (que cresce com o tempo);
      do Facebook, os que continuam sem modelo saem (podem não ser carros).
    Devolve o número de anúncios apagados.
    """
    reidentificar(db)
    limite = (datetime.now(UTC) - timedelta(days=config["retencao_dias"])).isoformat()
    favoritos = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'favoritos_v2'").fetchone()
    protegidos = " AND (origem, id) NOT IN (SELECT origem, id FROM favoritos_v2)" if favoritos else ""
    with db:
        apagados = db.execute(
            "DELETE FROM anuncios WHERE (atualizado_em < ? OR preco > ? "
            "OR (origem = 'facebook' AND modelo IS NULL))" + protegidos,
            (limite, config["preco_max_guardar"])).rowcount
    db.execute("PRAGMA optimize")
    if apagados > 1000:
        db.execute("VACUUM")  # devolve o espaço ao disco (e encolhe a cache do GitHub)
    return apagados


def reidentificar(db):
    """Dá modelo aos anúncios que ainda não o têm, com o catálogo atual. Devolve quantos ganharam modelo."""
    cat = catalogo(db)
    rows = db.execute("SELECT origem, id, titulo, marca, modelo_site FROM anuncios WHERE modelo IS NULL").fetchall()
    novos = []
    for origem, id_, titulo, marca, modelo_site in rows:
        a = identificar(db, {"origem": origem, "titulo": titulo or "", "marca": marca, "modelo_site": modelo_site},
                        cat)
        if a["modelo"]:
            novos.append((a["modelo"], a["modelo_fonte"], a["marca"], origem, id_))
    with db:
        db.executemany("UPDATE anuncios SET modelo = ?, modelo_fonte = ?, marca = ? WHERE origem = ? AND id = ?",
                       novos)
    return len(novos)


def fonte_vazia(db, origem):
    return not db.execute("SELECT 1 FROM anuncios WHERE origem = ? LIMIT 1", (origem,)).fetchone()


def duplicado(db, anuncio):
    if not (anuncio["km"] and anuncio["ano"]):
        return False
    return db.execute(
        "SELECT 1 FROM anuncios WHERE alertado = 1 AND modelo = ? AND ano = ? AND km = ? "
        "AND ABS(preco - ?) <= 250 AND origem != ?",
        (anuncio["modelo"], anuncio["ano"], anuncio["km"], anuncio["preco"], anuncio["origem"])).fetchone() is not None
