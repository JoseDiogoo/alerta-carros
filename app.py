"""Alerta Carros — painel Streamlit em tempo real com carros bons e baratos em Portugal."""

import threading
import time
from contextlib import closing

import pandas as pd
import streamlit as st

import monitor as m

st.set_page_config(page_title="Alerta Carros", page_icon="🚗", layout="wide")
st.markdown("""
<style>
  .preco {font-size: 1.6rem; font-weight: 700; margin: 0}
  .mediana {color: #6b7280; font-size: .85rem; margin: 0 0 .3rem}
  .selo {display: inline-block; padding: 2px 10px; border-radius: 999px; font-weight: 700;
         font-size: .85rem; background: #16a34a; color: white}
  .selo.suspeito {background: #d97706}
  .selo.neutro {background: #e5e7eb; color: #374151}
  .titulo {font-weight: 600; line-height: 1.25; height: 2.5em; overflow: hidden; margin: .4rem 0 .2rem}
  .detalhes {color: #4b5563; font-size: .9rem; margin: 0}
  div[data-testid="stImage"] img {border-radius: 10px; aspect-ratio: 4/3; object-fit: cover}
</style>""", unsafe_allow_html=True)

CONFIG_PATH = m.ROOT / "config.toml"
if not CONFIG_PATH.is_file():
    CONFIG_PATH = m.ROOT / "config.exemplo.toml"
BASE = m.load_config(CONFIG_PATH)
FONTES_RAPIDAS = [f for f in BASE["fontes"] if f != "facebook"]  # o Facebook é lento: fica na pesquisa
ATUALIZAR_SEGUNDOS = 60
SEM_FOTO = "https://placehold.co/640x480?text=Sem+foto"


# ---------- recolha partilhada por todas as sessões ----------

@st.cache_resource
def estado():
    db = m.abrir_db(m.ROOT / "painel.sqlite3", check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS favoritos (origem TEXT, id TEXT, modelo TEXT, titulo TEXT, "
               "preco INTEGER, ano INTEGER, km INTEGER, local TEXT, url TEXT, foto TEXT, guardado_em TEXT, "
               "PRIMARY KEY (origem, id))")
    if "foto" not in {row[1] for row in db.execute("PRAGMA table_info(favoritos)")}:
        db.execute("ALTER TABLE favoritos ADD COLUMN foto TEXT")
    db.commit()
    return {"db": db, "lock": threading.Lock(), "ultima": 0.0, "erros": {}, "semeado": False}


def recolher():
    s = estado()
    if not s["lock"].acquire(blocking=False):
        return  # outra sessão já está a recolher
    try:
        if not s["semeado"]:
            with st.spinner("Primeira vez: a juntar preços de referência do mercado (≈2 min)…"):
                m.semear(s["db"], {**BASE, "paginas_semente": 2})
            s["semeado"] = True
        if time.time() - s["ultima"] < ATUALIZAR_SEGUNDOS - 5:
            return
        for origem in FONTES_RAPIDAS:
            try:
                m.ciclo_paginado(s["db"], origem, BASE, so_guardar=True)
                s["erros"].pop(origem, None)
            except (RuntimeError, KeyError, TypeError, ValueError) as erro:
                s["erros"][origem] = str(erro)
        s["ultima"] = time.time()
    finally:
        s["lock"].release()


# ---------- avaliação (usa os filtros da barra lateral) ----------

def numero(valor):
    return None if valor is None or pd.isna(valor) else int(valor)


def avaliar(anuncios, config, db):
    """Junta mediana e desconto a cada anúncio que passa os filtros."""
    resultado = []
    for anuncio in anuncios:
        rule = m.escolher_regra(anuncio, config)
        if not anuncio["preco"] or not rule or m.excluido(anuncio, config) \
                or not m.dentro_dos_limites(anuncio, rule, config):
            continue
        mediana, n = m.mercado(db, anuncio, rule)
        ratio = anuncio["preco"] / mediana if mediana and n >= config["min_comparaveis"] else None
        resultado.append({**anuncio, "modelo": rule["nome"], "mediana": round(mediana) if ratio else None,
                          "desconto": None if ratio is None else round((1 - ratio) * 100),
                          "bom": ratio is not None and ratio <= config["desconto_mercado"],
                          "suspeito": ratio is not None and ratio < config["limite_burla"]})
    return resultado


def da_base(db, limite=400):
    rows = db.execute(
        "SELECT origem, id, titulo, preco, url, ano, km, local, foto FROM anuncios "
        "WHERE regra IS NOT NULL AND titulo IS NOT NULL ORDER BY visto_em DESC LIMIT ?", (limite,))
    return [m.carro(o, i, t, p, u, ano=numero(a), km=numero(k), local=l or "", foto=f)
            for o, i, t, p, u, a, k, l, f in rows]


def guardar_favorito(anuncio):
    db = estado()["db"]
    with db:
        db.execute("INSERT OR REPLACE INTO favoritos (origem, id, modelo, titulo, preco, ano, km, local, url, foto, "
                   "guardado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                   (anuncio["origem"], anuncio["id"], anuncio.get("modelo"), anuncio["titulo"],
                    anuncio["preco"], anuncio["ano"], anuncio["km"], anuncio["local"], anuncio["url"],
                    anuncio["foto"]))
    st.toast(f"⭐ Guardado: {anuncio['titulo'][:40]}")


def remover_favorito(anuncio):
    db = estado()["db"]
    with db:
        db.execute("DELETE FROM favoritos WHERE origem = ? AND id = ?", (anuncio["origem"], anuncio["id"]))


# ---------- cartões ----------

def euros(valor):
    return f"{int(valor):,} €".replace(",", " ")


def cartao(anuncio, chave, favorito=False):
    with st.container(border=True):
        st.image(anuncio["foto"] or SEM_FOTO, use_container_width=True)
        if anuncio.get("desconto") is not None and anuncio.get("bom"):
            classe = "selo suspeito" if anuncio["suspeito"] else "selo"
            texto = f"{'⚠️' if anuncio['suspeito'] else '🔥'} {anuncio['desconto']}% abaixo do mercado"
        elif anuncio.get("desconto") is not None:
            classe, texto = "selo neutro", f"{-anuncio['desconto']:+d}% vs mercado"
        else:
            classe, texto = "selo neutro", "sem referência"
        mediana = (f'<p class="mediana">mediana {euros(anuncio["mediana"])}</p>' if anuncio.get("mediana")
                   else '<p class="mediana">&nbsp;</p>')
        st.markdown(f'<span class="{classe}">{texto}</span>'
                    f'<p class="titulo">{anuncio["titulo"][:90]}</p>'
                    f'<p class="preco">{euros(anuncio["preco"])}</p>{mediana}', unsafe_allow_html=True)
        detalhes = [str(anuncio["ano"]) if anuncio["ano"] else None,
                    f"{anuncio['km']:,} km".replace(",", " ") if anuncio["km"] else None,
                    (anuncio["local"] or "").split(",")[0] or None, m.NOMES[anuncio["origem"]]]
        st.markdown(f'<p class="detalhes">{" · ".join(d for d in detalhes if d)}</p>', unsafe_allow_html=True)
        a, b = st.columns([3, 1])
        a.link_button("Ver anúncio ↗", anuncio["url"], use_container_width=True)
        if favorito:
            if b.button("🗑️", key=f"{chave}-rm", help="Remover dos favoritos", use_container_width=True):
                remover_favorito(anuncio)
                st.rerun()
        elif b.button("⭐", key=f"{chave}-fav", help="Guardar nos favoritos", use_container_width=True):
            guardar_favorito(anuncio)


def grelha(anuncios, prefixo, colunas=4, favorito=False):
    for inicio in range(0, len(anuncios), colunas):
        for coluna, anuncio in zip(st.columns(colunas), anuncios[inicio:inicio + colunas]):
            with coluna:
                cartao(anuncio, f"{prefixo}-{anuncio['origem']}-{anuncio['id']}", favorito)


# ---------- página ----------

with st.sidebar:
    st.header("⚙️ O que é bom e barato?")
    config = dict(BASE)
    config["preco_max"] = st.number_input("Preço máximo (€)", 1000, 200000, BASE["preco_max"], 500)
    config["preco_min"] = min(BASE["preco_min"], config["preco_max"] - 500)
    config["ano_min"] = st.number_input("Ano mínimo", 1990, 2026, BASE["ano_min"])
    config["km_max"] = st.number_input("Km máximos", 20000, 500000, BASE["km_max"], 10000)
    config["desconto_mercado"] = 1 - st.slider("Pelo menos X% abaixo do mercado", 5, 50,
                                               round((1 - BASE["desconto_mercado"]) * 100)) / 100
    modelos = st.multiselect("Modelos", [r["nome"] for r in BASE["regras"]],
                             default=[r["nome"] for r in BASE["regras"]])
    config["regras"] = [r for r in BASE["regras"] if r["nome"] in modelos]
    st.caption("Mediana = anúncios do mesmo modelo com ano ±1 em todos os sites.")

st.title("🚗 Alerta Carros")
st.caption(f"Carros bons e baratos em {', '.join(m.NOMES[f] for f in FONTES_RAPIDAS)} "
           "— atualiza sozinho a cada minuto.")

negocios, favoritos, procura = st.tabs(["🔥 Negócios agora", "⭐ Favoritos", "🔎 Pesquisar modelo"])


@st.fragment(run_every=ATUALIZAR_SEGUNDOS)
def painel():
    recolher()
    s = estado()
    anuncios = avaliar(da_base(s["db"]), config, s["db"])
    bons = sorted((a for a in anuncios if a["bom"]), key=lambda a: -a["desconto"])

    a, b, c, d = st.columns(4)
    total = s["db"].execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    a.metric("Anúncios analisados", f"{total:,}".replace(",", " "))
    b.metric("Bons negócios", len(bons))
    c.metric("Sites a funcionar", f"{len(FONTES_RAPIDAS) - len(s['erros'])} de {len(FONTES_RAPIDAS)}")
    d.metric("Atualizado às", time.strftime("%H:%M:%S", time.localtime(s["ultima"])) if s["ultima"] else "—")
    for origem, erro in s["erros"].items():
        st.warning(f"{m.NOMES[origem]}: {erro}")

    st.subheader("🔥 Bons negócios")
    if not bons:
        st.info("Ainda nenhum carro abaixo do mercado com estes filtros. Os novos aparecem aqui sozinhos.")
    else:
        if any(x["suspeito"] for x in bons):
            st.warning("⚠️ Preços a menos de metade do mercado: confirma documentos e nunca pagues sinal "
                       "antes de ver o carro.")
        grelha(bons[:12], "bons")

    st.subheader("🆕 Acabados de chegar")
    grelha(anuncios[:8], "novos")

    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Anúncios recolhidos por site**")
        por_site = pd.read_sql_query("SELECT origem, COUNT(*) AS anuncios FROM anuncios GROUP BY origem", s["db"])
        por_site["Site"] = por_site["origem"].map(m.NOMES)
        st.bar_chart(por_site, x="Site", y="anuncios", horizontal=True, y_label="", x_label="anúncios")
    with g2:
        st.markdown("**Preço mediano por modelo**")
        precos = pd.read_sql_query("SELECT regra, preco FROM anuncios WHERE regra IS NOT NULL", s["db"])
        precos = precos[precos["regra"].isin(modelos) & precos["preco"].between(config["preco_min"], 100000)]
        if not precos.empty:
            mediana = precos.groupby("regra")["preco"].median().sort_values().reset_index()
            st.bar_chart(mediana, x="regra", y="preco", horizontal=True, y_label="", x_label="€")


with negocios:
    painel()


@st.fragment(run_every=ATUALIZAR_SEGUNDOS)
def lista_favoritos():
    db = estado()["db"]
    rows = db.execute(
        "SELECT f.origem, f.id, f.titulo, coalesce(a.preco, f.preco), f.url, f.ano, f.km, f.local, f.foto, "
        "f.modelo, f.preco FROM favoritos f LEFT JOIN anuncios a USING (origem, id) "
        "ORDER BY f.guardado_em DESC").fetchall()
    if not rows:
        st.info("Ainda sem favoritos. Carrega na ⭐ de um carro para o guardares aqui.")
        return
    anuncios = []
    for o, i, t, p, u, a, k, l, f, modelo, guardado in rows:
        anuncio = m.carro(o, i, t, p, u, ano=a, km=k, local=l or "", foto=f, modelo=modelo)
        mediana, n = m.mercado(db, anuncio, {"nome": modelo}) if modelo else (None, 0)
        ratio = p / mediana if mediana and n >= config["min_comparaveis"] else None
        anuncio.update(mediana=round(mediana) if ratio else None,
                       desconto=None if ratio is None else round((1 - ratio) * 100),
                       bom=ratio is not None and ratio <= config["desconto_mercado"],
                       suspeito=ratio is not None and ratio < config["limite_burla"], guardado=guardado)
        anuncios.append(anuncio)
    baixaram = [x for x in anuncios if x["preco"] < x["guardado"]]
    a, b, c = st.columns(3)
    a.metric("Favoritos", len(anuncios))
    b.metric("Baixaram de preço", len(baixaram))
    c.metric("Mais barato", euros(min(x["preco"] for x in anuncios)))
    for x in baixaram:
        st.success(f"📉 {x['titulo'][:60]}: {euros(x['guardado'])} → {euros(x['preco'])}")
    grelha(anuncios, "fav", favorito=True)


with favoritos:
    lista_favoritos()


@st.cache_data(ttl=600, show_spinner=False)
def pesquisar(termo, caminho_sv):
    anuncios, erros = [], []
    fontes = [("OLX", lambda p: m.olx(p, termo))]
    if caminho_sv:
        fontes.append(("Standvirtual", lambda p: m.standvirtual(p, caminho_sv)))
    for nome, buscar in fontes:
        try:
            for pagina in (1, 2, 3):
                anuncios += list(buscar(pagina))
        except RuntimeError as erro:
            erros.append(f"{nome}: {erro}")
    for cidade in BASE["facebook_cidades"]:
        try:
            anuncios += list(m.facebook(termo, cidade, 500, 200000))
        except RuntimeError as erro:
            erros.append(f"Facebook ({cidade}): {erro}")
    return anuncios, erros


with procura:
    st.caption("Lê agora o OLX, o Standvirtual e o Facebook para um modelo à tua escolha.")
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    texto = c1.text_input("Marca e modelo", "Toyota Yaris")
    if c2.button("Pesquisar", type="primary", use_container_width=True) and texto.strip():
        st.session_state["pesquisa"] = texto.strip()
    if st.session_state.get("pesquisa"):
        texto = st.session_state["pesquisa"]
        partes = m.words(texto).split()
        rule = {"nome": texto, "termos": [" ".join(partes[1:]) or partes[0]]}
        caminho = f"{partes[0]}/{'-'.join(partes[1:])}" if len(partes) > 1 else ""
        with st.spinner("A ler os sites…"):
            lidos, erros = pesquisar(" ".join(partes), caminho)
        for erro in erros:
            st.warning(erro)
        cfg = {**config, "regras": [rule]}
        with closing(m.abrir_db(":memory:")) as db:
            for anuncio in lidos:
                m.processar(db, dict(anuncio), cfg, referencia=True)
            resultado = list({x["url"]: x for x in avaliar(lidos, cfg, db)}.values())
        resultado.sort(key=lambda x: -(x["desconto"] if x["desconto"] is not None else -999))
        a, b, c = st.columns(3)
        a.metric("Anúncios lidos", len(lidos))
        b.metric("Dentro dos filtros", len(resultado))
        c.metric("Bons negócios", sum(1 for x in resultado if x["bom"]))
        tabela = pd.DataFrame([x for x in resultado if x["ano"]])
        if len(tabela) and tabela["ano"].nunique() > 1:
            st.markdown("**Preço mediano por ano**")
            st.bar_chart(tabela.groupby("ano")["preco"].median(), x_label="ano", y_label="€")
        grelha(resultado[:24], "pesq")
