"""Alerta Carros — os melhores negócios de carros em Portugal, com margem estimada, em tempo real."""

import threading
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import UTC, datetime, timedelta
from html import escape

import pandas as pd
import streamlit as st

from alerta import avaliacao as av
from alerta.anuncio import NOMES, carro, completar_motor
from alerta.config import ROOT, load_config
from alerta.db import abrir_db, limpar
from alerta.fontes import facebook, olx, standvirtual
from alerta.fontes.http import ainda_existe
from alerta.mercado import analisar as analisar_mercado
from alerta.negocio import excluido, melhor
from alerta.catalogo import identificar
from alerta.texto import euros, words
from alerta.vigia import processar, semear, vigiar

st.set_page_config(page_title="Alerta Carros", layout="wide")
st.markdown("""
<style>
  .hero h1 {font-size: 2.4rem; font-weight: 800; margin: 0}
  .hero h1 span {color: #e3242b}
  .hero p {color: #a3a3a3; margin: .2rem 0 1rem}
  .marca {font-weight: 700; color: #fff; margin: .5rem 0 0}
  .titulo {color: #a3a3a3; font-size: .85rem; line-height: 1.25em; height: 2.5em; overflow: hidden; margin: 0 0 .3rem;
           display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical}
  .preco {font-size: 1.5rem; font-weight: 800; margin: 0; color: #e3242b}
  .mercado {color: #a3a3a3; font-size: .8rem; margin: 0}
  .margem {color: #22c55e; font-weight: 700; margin: .1rem 0 .3rem}
  .margem.neg {color: #f87171}
  .selo {display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .75rem;
         background: #e3242b; color: #fff}
  .selo.suspeito {background: #d97706}
  .selo.neutro {background: #333; color: #d4d4d4}
  .selo.avaria {background: #facc15; color: #111; font-weight: 700}
  .detalhes {color: #a3a3a3; font-size: .8rem; margin: 0 0 .4rem; min-height: 2.2em}
  div[data-testid="stImage"] img {border-radius: 10px; aspect-ratio: 4/3; object-fit: cover}
  /* sem ecrã cinzento enquanto procura */
  [data-stale="true"], .stale-element {opacity: 1 !important; transition: none !important}
  div[data-testid="stStatusWidget"] {visibility: hidden}
  /* telemóvel: números 2×2, botões lado a lado, margens pequenas */
  @media (max-width: 640px) {
    .block-container {padding: 3rem .75rem 2rem !important}
    .hero h1 {font-size: 1.8rem}
    .hero p {font-size: .85rem}
    [class*="st-key-metricas"] [data-testid="stHorizontalBlock"],
    [class*="st-key-botoes"] [data-testid="stHorizontalBlock"] {flex-wrap: wrap !important; flex-direction: row !important;
                                                                 gap: .5rem !important}
    [class*="st-key-metricas"] [data-testid="stColumn"] {min-width: calc(50% - .5rem) !important;
                                                          flex: 1 1 calc(50% - .5rem) !important}
    [class*="st-key-botoes"] [data-testid="stColumn"] {min-width: 0 !important; flex: 1 1 0 !important}
    [data-testid="stMetricValue"] {font-size: 1.4rem !important}
    [data-testid="stTabs"] button {padding: 0 .4rem !important}
  }
</style>""", unsafe_allow_html=True)

CONFIG_PATH = ROOT / "config.toml"
if not CONFIG_PATH.is_file():
    CONFIG_PATH = ROOT / "config.exemplo.toml"
BASE = load_config(CONFIG_PATH)
FONTES = BASE["fontes"]
ATUALIZAR_SEGUNDOS = 15
SEM_FOTO = "https://placehold.co/640x480/1f2937/9ca3af?text=Sem+foto"
COLUNAS = ["origem", "id", "titulo", "preco", "url", "ano", "km", "local", "foto", "modelo", "combustivel",
           "caixa", "profissional", "importado", "sinais", "extras", "cilindrada", "potencia", "carrocaria",
           "marca", "modelo_site", "modelo_fonte"]
CAMINHO_DB = ROOT / "painel.sqlite3"


# ---------- recolha em segundo plano, partilhada por todas as sessões ----------

def recolha_continua(s):
    """Junta o mercado e depois lê todos os sites em paralelo, sem parar (sem alertas: isso é o monitor)."""
    db = abrir_db(CAMINHO_DB)
    semear(db, {**BASE, "paginas_semente": 8}, aviso=lambda *_: None)
    db.close()
    s["semeado"] = True
    for origem in [*FONTES, "mercado"]:
        threading.Thread(target=vigiar, args=(origem, CAMINHO_DB, BASE, threading.Event()),
                         kwargs={"so_guardar": True, "erros": s["erros"]}, daemon=True).start()


@st.cache_resource
def estado():
    esquema = 7  # mudar quando a tabela ganha colunas: força a reabrir a base na Streamlit Cloud
    db = abrir_db(CAMINHO_DB, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS favoritos_v2 (origem TEXT, id TEXT, guardado_preco INTEGER, "
               "guardado_em TEXT, PRIMARY KEY (origem, id))")
    db.commit()
    limpar(db, BASE)
    s = {"db": db, "erros": {}, "semeado": False}
    threading.Thread(target=recolha_continua, args=(s,), daemon=True).start()
    return s


# ---------- filtros ----------

ORDENS = {
    "Melhor negócio (pontuação)": lambda a: -(a["pontuacao"] if a["pontuacao"] is not None else -1),
    "Maior margem de lucro": lambda a: -(a["margem"] if a["margem"] is not None else -10**9),
    "Maior desconto face ao mercado": lambda a: -(a["desconto"] if a["desconto"] is not None else -10**9),
    "Mais barato": lambda a: a["preco"],
    "Mais caro": lambda a: -a["preco"],
    "Menos quilómetros": lambda a: a["km"] or 10**9,
    "Mais novo": lambda a: -(a["ano"] or 0),
    "Mais recente no site": lambda a: 0,
}


def lista(sql, args=()):
    return [r[0] for r in estado()["db"].execute(sql, args) if r[0]]


def entre(valor, minimo, maximo):
    """Valor do config.toml dentro dos limites do controlo (senão o Streamlit dá erro)."""
    return max(minimo, min(maximo, valor))


ANO_MAX = time.localtime().tm_year
PRECO_TETO = BASE["preco_max_guardar"]  # acima disto a base não guarda anúncios


@st.cache_data(ttl=600, show_spinner=False)
def modelos_conhecidos():
    """Modelos na base, por ordem alfabética. Em cache e estável: se as opções de um filtro mudassem a cada
    recolha (ex.: ordenadas pelo número de anúncios), o Streamlit apagava o que o utilizador escolheu."""
    return lista("SELECT DISTINCT modelo FROM anuncios WHERE modelo IS NOT NULL ORDER BY modelo")


@st.cache_data(ttl=600, show_spinner=False)
def marcas_conhecidas():
    return lista("SELECT DISTINCT marca FROM anuncios WHERE marca IS NOT NULL ORDER BY marca")


def com_escolhidos(opcoes, chave):
    """As opções incluem sempre o que já está escolhido (senão a escolha desaparecia)."""
    return sorted(set(opcoes) | set(st.session_state.get(chave) or []))


with st.sidebar:
    st.header("Filtros")
    f = {}
    f["preco"] = st.slider("Preço (€)", 300, PRECO_TETO, (entre(BASE["preco_min"], 300, PRECO_TETO - 100),
                                                         entre(BASE["preco_max"], 400, PRECO_TETO)), 100,
                           key="f_preco")
    f["ano"] = st.slider("Ano", 1995, ANO_MAX, (entre(BASE["ano_min"], 1995, ANO_MAX), ANO_MAX), key="f_ano")
    f["km"] = st.slider("Km máximos", 10000, 400000, entre(BASE["km_max"], 10000, 400000), 10000, key="f_km")
    todos_modelos = modelos_conhecidos()
    marcas = st.multiselect("Marca", com_escolhidos(marcas_conhecidas(), "f_marcas"),
                            format_func=str.title, placeholder="Todas", key="f_marcas")
    modelos = st.multiselect("Modelo", com_escolhidos([x for x in todos_modelos if not marcas
                                                       or x.split()[0] in marcas], "f_modelos"),
                             format_func=str.title, placeholder="Todos", key="f_modelos")
    f["combustivel"] = st.multiselect("Combustível", ["Gasolina", "Gasóleo", "Híbrido", "Elétrico", "GPL"],
                                      placeholder="Todos", key="f_combustivel")
    f["caixa"] = st.multiselect("Caixa", ["Manual", "Automática"], placeholder="Todas", key="f_caixa")
    f["vendedor"] = st.radio("Vendedor", ["Todos", "Particular", "Profissional"], horizontal=True, key="f_vendedor")
    f["sites"] = st.multiselect("Sites", FONTES, format_func=NOMES.get, placeholder="Todos", key="f_sites")
    f["local"] = st.text_input("Zona", placeholder="ex.: Lisboa, Porto, Braga", key="f_local")
    f["nacionais"] = st.checkbox("Só carros nacionais", BASE["apenas_nacionais"], key="f_nacionais")
    st.header("Negócio")
    f["custos"] = st.number_input("Custos de revenda (€)", 0, 10000, entre(BASE["custos_revenda"], 0, 10000), 100,
                                  help="Inspeção, limpeza, pequenas reparações e anúncio", key="f_custos")
    f["comparaveis"] = st.slider("Anúncios comparáveis mínimos", 3, 30, entre(BASE["min_comparaveis"], 3, 30),
                                 help="Mais anúncios = valor de mercado mais fiável", key="f_comparaveis")
    f["excluir"] = st.toggle("Esconder carros para peças e com problemas legais", True, key="f_excluir")
    f["avaria"] = st.toggle("Mostrar carros com avaria", True, key="f_avaria",
                            help="Aparecem com a etiqueta AVARIA. O custo da reparação não é estimado.")
    if st.button("Repor filtros", width="stretch"):
        for chave in [k for k in st.session_state if str(k).startswith("f_")]:
            del st.session_state[chave]
        st.rerun()
    st.caption(f"Telegram: só os melhores — ≥{round(BASE['desconto_telegram'] * 100)}% abaixo do mercado "
               f"(≥{round(BASE['desconto_foco'] * 100)}% em {', '.join(x.title() for x in BASE['marcas_foco'])}), "
               f"≥{BASE['comparaveis_telegram']} comparáveis e pontuação ≥{BASE['pontuacao_minima']}.")


def candidatos(limite=3000, marca=None):
    """Anúncios que passam os filtros de base (SQL), mais recentes primeiro. marca: só essa marca."""
    marcas_sel, modelos_sel = ([marca], []) if marca else (marcas, modelos)
    sql = [f"SELECT {', '.join(COLUNAS)} FROM anuncios WHERE preco BETWEEN ? AND ? "
           "AND (ano IS NULL OR ano BETWEEN ? AND ?) AND (km IS NULL OR km <= ?)"]
    args = [*f["preco"], *f["ano"], f["km"]]
    for coluna, valores in (("modelo", modelos_sel), ("combustivel", f["combustivel"]), ("caixa", f["caixa"]),
                            ("origem", f["sites"])):
        if valores:
            sql.append(f"AND {coluna} IN ({','.join('?' * len(valores))})")
            args += valores
    if marcas_sel:
        sql.append(f"AND marca IN ({','.join('?' * len(marcas_sel))})")
        args += marcas_sel
    if f["vendedor"] != "Todos":
        sql.append("AND profissional = ?")
        args.append(int(f["vendedor"] == "Profissional"))
    if f["local"].strip():
        sql.append("AND local LIKE ?")
        args.append(f"%{f['local'].strip()}%")
    if f["nacionais"]:
        sql.append("AND (importado IS NULL OR importado = 0)")
    # eliminados ficam na base (contam para o mercado) mas não aparecem; o Facebook não deixa abrir o
    # anúncio sem login, por isso só aparecem os que a pesquisa voltou a mostrar nas últimas 48 h
    sql.append("AND (removido IS NULL OR removido = 0) AND (origem != 'facebook' OR atualizado_em >= ?)")
    args.append((datetime.now(UTC) - timedelta(hours=48)).isoformat())
    sql.append("ORDER BY visto_em DESC LIMIT ?")
    args.append(limite)
    regras = {**BASE, "excluir": BASE["excluir"] if f["excluir"] else [],
              "excluir_texto": BASE["excluir_texto"] if f["excluir"] else []}
    anuncios = (carro(**dict(zip(COLUNAS, r))) for r in estado()["db"].execute(" ".join(sql), args))
    unicos = {}  # o mesmo carro publicado em vários sites aparece uma vez
    for a in anuncios:
        if not excluido(a, regras):
            unicos.setdefault((a["modelo"] or (a["origem"], a["id"]), a["ano"], a["km"] or (a["origem"], a["id"]),
                               a["preco"]), a)
    return list(unicos.values())


CACHE_ANALISE_SEGUNDOS = 60  # o mercado muda devagar; o painel redesenha-se de 15 em 15 s


@st.cache_resource
def cache_analises():
    """Avaliações partilhadas por todos os separadores e visitantes: (anúncio, custos) -> (quando, resultado)."""
    return {}


def analisar(anuncio, db):
    """A mesma avaliação do monitor (alertas do Telegram), com os custos e comparáveis do painel."""
    cache = cache_analises()
    chave = (anuncio["origem"], anuncio["id"], anuncio["preco"], anuncio["km"], anuncio["ano"],
             f["custos"], f["comparaveis"])
    guardado = cache.get(chave)
    if guardado and time.monotonic() - guardado[0] < CACHE_ANALISE_SEGUNDOS:
        return guardado[1]
    anuncio = {**anuncio, "sinais": av.carregar(anuncio.get("sinais")) or None,
               "extras": av.carregar(anuncio.get("extras"))}
    completar_motor(anuncio)
    analise = analisar_mercado(db, anuncio, {**BASE, "custos_revenda": f["custos"], "min_comparaveis": f["comparaveis"]})
    resultado = {**anuncio, **analise, "bom": melhor(anuncio, analise, BASE)}  # bom = iria para o Telegram
    if len(cache) > 50000:
        cache.clear()
    cache[chave] = (time.monotonic(), resultado)
    return resultado


def analisados(marca=None):
    """Todos os carros dentro dos filtros, avaliados (com avaria incluídos, se o filtro deixar)."""
    db = estado()["db"]
    lista_ = [analisar(a, db) for a in candidatos(marca=marca)]
    return [a for a in lista_ if (f["avaria"] or not a["avaria"]["tem"])
            and not (f["excluir"] and a["avaria"]["inviaveis"])]


# ---------- cartões ----------



def cartao(anuncio, chave, favorito=False):
    with st.container(border=True):
        st.image(anuncio["foto"] or SEM_FOTO, width="stretch")
        if anuncio["margem"] is None:
            selo = ('<span class="selo neutro">modelo por confirmar</span>' if not anuncio.get("modelo") else
                    '<span class="selo neutro">a recolher dados de mercado</span>')
        elif anuncio.get("irreal"):
            selo = f'<span class="selo suspeito">{anuncio["desconto"]}% abaixo · preço irreal</span>'
        elif anuncio["suspeito"]:
            selo = f'<span class="selo suspeito">{anuncio["desconto"]}% abaixo · confirmar</span>'
        elif anuncio["bom"]:
            selo = f'<span class="selo">★ {anuncio["desconto"]}% abaixo do mercado</span>'
        elif anuncio["desconto"] > 0:
            selo = f'<span class="selo neutro">{anuncio["desconto"]}% abaixo do mercado</span>'
        else:
            selo = f'<span class="selo neutro">{-anuncio["desconto"]:+d}% vs mercado</span>'
        if anuncio["avaria"]["tem"]:
            itens = ", ".join(anuncio["avaria"]["itens"][:3])
            selo += f' <span class="selo avaria" title="{escape(itens)}">⚠ AVARIA: {escape(itens[:40])}</span>'
        if anuncio["margem"] is None:
            margem = '<p class="margem">&nbsp;</p>'
        else:
            sinal = "+" if anuncio["margem"] >= 0 else "−"
            classe = "margem" if anuncio["margem"] >= 0 else "margem neg"
            margem = f'<p class="{classe}">margem {sinal}{euros(abs(anuncio["margem"]))}</p>'
        mercado = (f'<p class="mercado" title="{escape(anuncio["nivel"] or "")}">mercado ~{euros(anuncio["mediana"])}'
                   f'{" (prudente)" if anuncio.get("prudente_usado") else ""} · {anuncio["n"]} comparáveis</p>' if anuncio["mediana"] else '<p class="mercado">&nbsp;</p>')
        # o texto vem dos sites: escapar antes de o pôr em HTML
        modelo = escape((anuncio.get("modelo") or anuncio["titulo"]).title())
        st.markdown(f'{selo}<p class="marca">{modelo}</p><p class="titulo">{escape(anuncio["titulo"][:90])}</p>'
                    f'<p class="preco">{euros(anuncio["preco"])}</p>{mercado}{margem}',
                    unsafe_allow_html=True)
        detalhes = [str(anuncio["ano"]) if anuncio["ano"] else None,
                    f"{anuncio['km']:,} km".replace(",", " ") if anuncio["km"] else None,
                    f"{anuncio['cilindrada'] / 1000:.1f}" if anuncio.get("cilindrada") else None,
                    f"{anuncio['potencia']} cv" if anuncio.get("potencia") else None,
                    anuncio["combustivel"], anuncio["caixa"],
                    (anuncio["local"] or "").split(",")[0] or None, NOMES[anuncio["origem"]]]
        st.markdown(f'<p class="detalhes">{escape(" · ".join(d for d in detalhes if d))}</p>',
                    unsafe_allow_html=True)
        if anuncio.get("pontuacao") is not None:
            st.markdown(f'<p class="detalhes"><b>Pontuação {anuncio["pontuacao"]}/100</b></p>',
                        unsafe_allow_html=True)
            if anuncio["fortes"]:
                st.caption("A favor: " + ", ".join(anuncio["fortes"][:4]))
            if anuncio["alertas"]:
                st.caption("Atenção: " + ", ".join(anuncio["alertas"][:3]))
        if anuncio["avaria"]["tem"] and anuncio["margem"] is not None:
            st.caption("🔧 Margem sem contar a reparação: confirmar o custo com um mecânico.")
        a, b = st.container(key=f"botoes-{chave}").columns([3, 2])
        a.link_button("Ver anúncio", anuncio["url"], width="stretch")
        db = estado()["db"]
        if favorito:
            if b.button("Remover", key=f"{chave}-rm", help="Remover dos favoritos", width="stretch"):
                with db:
                    db.execute("DELETE FROM favoritos_v2 WHERE origem = ? AND id = ?", (anuncio["origem"], anuncio["id"]))
                st.rerun()
        elif b.button("Guardar", key=f"{chave}-fav", help="Guardar nos favoritos", width="stretch"):
            with db:
                db.execute("INSERT OR REPLACE INTO favoritos_v2 VALUES (?, ?, ?, datetime('now'))",
                           (anuncio["origem"], anuncio["id"], anuncio["preco"]))
            st.session_state["aviso"] = f"Guardado nos favoritos: {anuncio['titulo'][:40]}"
            st.rerun(scope="app")  # o separador Favoritos é outro fragmento: redesenha a página toda


def vivos(anuncios, limite):
    """Confirma no site, antes de mostrar, que os anúncios ainda existem (cada um no máximo de 30 em 30 min).
    Os eliminados ficam marcados e saem da lista; os seguintes ocupam o lugar."""
    db, ficam = estado()["db"], []
    recente = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    for inicio in range(0, len(anuncios), limite):
        lote = anuncios[inicio:inicio + limite]
        chaves = [(a["origem"], a["id"]) for a in lote]
        marcas_db = {(o, i): (r, v) for o, i, r, v in db.execute(
            "SELECT origem, id, coalesce(removido, 0), coalesce(verificado_em >= ?, 0) FROM anuncios "
            f"WHERE (origem, id) IN (VALUES {','.join(['(?, ?)'] * len(chaves))})",
            [recente, *[x for c in chaves for x in c]])}
        rever = [a for a in lote if a["origem"] != "facebook" and not marcas_db.get((a["origem"], a["id"]), (0, 0))[1]]
        with ThreadPoolExecutor(8) as pool:
            for a, existe in zip(rever, pool.map(lambda a: ainda_existe(a["url"]), rever)):
                if existe is not None:
                    marcas_db[(a["origem"], a["id"])] = (int(not existe), 1)
                    with db:
                        db.execute("UPDATE anuncios SET removido = ?, verificado_em = ? WHERE origem = ? AND id = ?",
                                   (int(not existe), datetime.now(UTC).isoformat(), a["origem"], a["id"]))
        ficam += [a for a in lote if not marcas_db.get((a["origem"], a["id"]), (0, 0))[0]]
        if len(ficam) >= limite:
            break
    return ficam[:limite]


def grelha(anuncios, prefixo, colunas=4, favorito=False, limite=24):
    anuncios = vivos(anuncios, limite)
    for inicio in range(0, len(anuncios), colunas):
        for coluna, anuncio in zip(st.columns(colunas), anuncios[inicio:inicio + colunas]):
            with coluna:
                cartao(anuncio, f"{prefixo}-{anuncio['origem']}-{anuncio['id']}", favorito)


# ---------- página ----------

st.markdown('<div class="hero"><h1>Alerta <span>Carros</span></h1><p>Foco em Toyota: os mais baratos face ao valor de '
            'mercado em Standvirtual, OLX, CustoJusto, Auto.pt, AutoX e Facebook Marketplace, com margem de lucro '
            f'estimada e atualização a cada {ATUALIZAR_SEGUNDOS} segundos.</p></div>', unsafe_allow_html=True)
if aviso := st.session_state.pop("aviso", None):
    st.toast(aviso)

toyota_tab, negocios, favoritos, mercado_tab, procura = st.tabs(
    ["Toyota (foco)", "Outras marcas", "Favoritos", "Mercado", "Pesquisar modelo"])


@st.fragment(run_every=ATUALIZAR_SEGUNDOS)
def painel():
    s = estado()
    if not s["semeado"]:
        st.info("Primeira vez: a conhecer o mercado nos 6 sites (cerca de 1 minuto). A página atualiza sozinha.")
    todos = analisados()

    a, b, c, d = st.container(key="metricas-geral").columns(4)
    total = s["db"].execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    a.metric("Carros conhecidos", f"{total:,}".replace(",", " "))
    b.metric("Nos filtros", len(todos))
    c.metric("Melhores (Telegram)", sum(1 for x in todos if x["bom"]),
             help="Cumprem o critério do Telegram: muito abaixo de um valor de mercado fiável")
    ultimo = s["db"].execute("SELECT MAX(atualizado_em) FROM anuncios").fetchone()[0]
    d.metric("Último anúncio lido", datetime.fromisoformat(ultimo).astimezone().strftime("%H:%M:%S") if ultimo else "—")
    for origem, erro in list(s["erros"].items()):
        st.warning(f"{NOMES.get(origem, 'Mercado')}: {erro}")
    with st.expander("Estado dos sites"):
        st.caption("Cada site lido nas últimas 24 h. Os carros com modelo por confirmar aparecem no painel, mas "
                   "não entram no valor de mercado nem vão para o Telegram.")
        desde = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        por_site = {r[0]: r[1:] for r in s["db"].execute(
            "SELECT origem, COUNT(*), SUM(modelo IS NOT NULL), MAX(atualizado_em) FROM anuncios "
            "WHERE atualizado_em >= ? GROUP BY origem", (desde,))}
        st.dataframe(pd.DataFrame([{
            "site": NOMES[o], "anúncios (24 h)": por_site.get(o, (0, 0, None))[0],
            "com modelo certo": por_site.get(o, (0, 0, None))[1] or 0,
            "última leitura": (datetime.fromisoformat(por_site[o][2]).astimezone().strftime("%d/%m %H:%M")
                               if o in por_site else "—"),
            "estado": f"erro: {s['erros'][o]}" if o in s["erros"] else ("ok" if o in por_site else "sem anúncios"),
        } for o in FONTES]), hide_index=True, width="stretch")
    o1, o2, o3 = st.columns([2, 1, 2], vertical_alignment="bottom")
    ordem = o1.selectbox("Ordenar por", list(ORDENS), key="ordem")
    quantos = o2.selectbox("Mostrar", [24, 48, 96, 200], key="quantos")
    so_melhores = o3.toggle("Só os melhores (critério do Telegram)", False, key="so_melhores")
    escolhidos = [x for x in todos if x["bom"]] if so_melhores else todos
    bons = sorted(escolhidos, key=ORDENS[ordem])  # sort estável: "mais recente" mantém a ordem da base

    if not bons:
        st.info("Nenhum carro com estes filtros. O mercado conhecido cresce sozinho; experimenta alargar os filtros.")
    else:
        if any(x["suspeito"] for x in bons[:quantos]):
            st.warning("Preços a menos de metade do mercado: confirma documentos e nunca pagues sinal "
                       "antes de ver o carro.")
        grelha(bons, "bons", limite=quantos)
        with st.expander(f"Ver os {len(bons)} carros em tabela"):
            st.dataframe(
                pd.DataFrame(bons).assign(avaria=lambda d: d["avaria"].map(lambda v: ", ".join(v["itens"])))[
                    ["margem", "desconto", "preco", "mediana", "venda", "n", "nivel", "pontuacao", "avaria",
                                    "modelo", "titulo", "ano", "km", "cilindrada", "potencia", "combustivel",
                                    "caixa", "local", "origem", "url"]],
                hide_index=True, width="stretch",
                column_config={"url": st.column_config.LinkColumn("anúncio", display_text="abrir"),
                               "margem": st.column_config.NumberColumn("margem", format="%d €"),
                               "preco": st.column_config.NumberColumn("preço", format="%d €"),
                               "mediana": st.column_config.NumberColumn("mercado", format="%d €"),
                               "venda": st.column_config.NumberColumn("venda provável", format="%d €"),
                               "nivel": st.column_config.TextColumn("comparado com"),
                               "desconto": st.column_config.NumberColumn("desconto", format="%d %%"),
                               "n": st.column_config.NumberColumn("comparáveis"),
                               "ano": st.column_config.NumberColumn(format="%d")})

    st.subheader("Acabados de chegar")
    grelha(todos, "novos", limite=8)


with negocios:
    painel()


@st.fragment(run_every=ATUALIZAR_SEGUNDOS)
def lista_favoritos():
    db = estado()["db"]
    rows = db.execute(f"SELECT {', '.join('a.' + c for c in COLUNAS)}, f.guardado_preco FROM favoritos_v2 f "
                      "JOIN anuncios a USING (origem, id) ORDER BY f.guardado_em DESC").fetchall()
    if not rows:
        st.info("Ainda sem favoritos. Carrega em \"Guardar\" num carro para o guardares aqui.")
        return
    anuncios = [{**analisar(carro(**dict(zip(COLUNAS, r[:-1]))), db), "guardado": r[-1]} for r in rows]
    baixaram = [x for x in anuncios if x["preco"] < x["guardado"]]
    a, b, c = st.columns(3)
    a.metric("Favoritos", len(anuncios))
    b.metric("Baixaram de preço", len(baixaram))
    c.metric("Margem total estimada", euros(sum(x["margem"] or 0 for x in anuncios)))
    for x in baixaram:
        st.success(f"Baixou de preço: {x['titulo'][:60]}: {euros(x['guardado'])} → {euros(x['preco'])}")
    grelha(anuncios, "fav", favorito=True, limite=500)


@st.fragment(run_every=ATUALIZAR_SEGUNDOS)
def painel_toyota():
    """Só Toyota (todos dentro dos filtros), separados em prontos a vender e com avaria."""
    toyotas = sorted(analisados(marca="toyota"), key=ORDENS["Melhor negócio (pontuação)"])
    prontos = [t for t in toyotas if not t["avaria"]["tem"]]
    com_avaria = [t for t in toyotas if t["avaria"]["tem"]]
    a, b, c, d = st.container(key="metricas-toyota").columns(4)
    a.metric("Toyotas nos filtros", len(toyotas))
    b.metric("Prontos a vender", len(prontos))
    c.metric("Com avaria", len(com_avaria))
    d.metric("Melhores (Telegram)", sum(1 for t in toyotas if t["bom"]),
             help=f"Toyota com pelo menos {round(BASE['desconto_foco'] * 100)}% abaixo do mercado")
    st.caption("Ordenados pela pontuação. Nos carros com avaria a margem não conta com a reparação: "
               "confirma sempre com um mecânico antes de comprar.")
    st.subheader(f"Prontos a vender ({len(prontos)})")
    if prontos:
        grelha(prontos, "toy-ok", limite=48)
    else:
        st.info("Nenhum Toyota pronto a vender com estes filtros, por agora.")
    st.subheader(f"Com avaria ({len(com_avaria)})")
    if com_avaria:
        grelha(com_avaria, "toy-rep", limite=48)
    else:
        st.info("Nenhum Toyota com avaria com estes filtros, por agora.")


with toyota_tab:
    painel_toyota()


with favoritos:
    lista_favoritos()


with mercado_tab:
    db = estado()["db"]
    resumo = pd.read_sql_query(
        "SELECT modelo, COUNT(*) AS anuncios, CAST(AVG(preco) AS INT) AS preco_medio, MIN(ano) AS desde, "
        "MAX(ano) AS ate FROM anuncios WHERE modelo IS NOT NULL AND preco >= 300 GROUP BY modelo "
        "HAVING COUNT(*) >= 5 ORDER BY anuncios DESC", db)
    st.caption("Quanto se pede por cada modelo agora, a partir de todos os anúncios recolhidos. "
               "São preços pedidos; os de venda costumam ficar um pouco abaixo.")
    escolha = st.selectbox("Modelo", resumo["modelo"], format_func=str.title, index=None,
                           placeholder="Escolhe um modelo para ver os preços por ano")
    if escolha:
        dados = pd.read_sql_query("SELECT ano, km, preco FROM anuncios WHERE modelo = ? AND ano IS NOT NULL "
                                  "AND preco >= 300", db, params=(escolha,))
        por_ano = dados.groupby("ano").agg(anuncios=("preco", "size"), mediana=("preco", "median"),
                                           minimo=("preco", "min"), maximo=("preco", "max"),
                                           km_mediano=("km", "median")).reset_index()
        x, y = st.columns([2, 3])
        x.dataframe(por_ano, hide_index=True, width="stretch",
                    column_config={**{c: st.column_config.NumberColumn(format="%d €")
                                      for c in ("mediana", "minimo", "maximo")},
                                   "ano": st.column_config.NumberColumn(format="%d"),
                                   "km_mediano": st.column_config.NumberColumn("km mediano", format="%d")})
        y.markdown(f"**{escolha.title()} — preço mediano por ano**")
        y.line_chart(por_ano.assign(ano=por_ano["ano"].astype(str)), x="ano", y="mediana", y_label="€",
                     x_label="ano")  # texto: sem "2,015" no eixo
    st.markdown("**Modelos com mais anúncios**")
    st.dataframe(resumo.head(100), hide_index=True, width="stretch",
                 column_config={"preco_medio": st.column_config.NumberColumn("preço médio", format="%d €")})


@st.cache_data(ttl=600, show_spinner=False)
def pesquisar(termo, caminho_sv):
    anuncios, erros = [], []
    fontes = [("OLX", lambda p: olx(p, termo))]
    if caminho_sv:
        fontes.append(("Standvirtual", lambda p: standvirtual(p, caminho_sv)))
    for nome, buscar in fontes:
        try:
            for pagina in (1, 2, 3):
                anuncios += list(buscar(pagina))
        except RuntimeError as erro:
            erros.append(f"{nome}: {erro}")
    for cidade in BASE["facebook_cidades"]:
        try:
            anuncios += list(facebook(cidade, 500, 200000, termo=termo))
        except RuntimeError as erro:
            erros.append(f"Facebook ({cidade}): {erro}")
    return anuncios, erros


with procura:
    st.caption("Lê agora o OLX, o Standvirtual e o Facebook para um modelo à tua escolha e junta-os ao mercado.")
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    texto = c1.text_input("Marca e modelo", "Toyota Yaris")
    if c2.button("Pesquisar", type="primary", width="stretch") and texto.strip():
        st.session_state["pesquisa"] = texto.strip()
    if st.session_state.get("pesquisa"):
        partes = words(st.session_state["pesquisa"]).split()
        caminho = f"{partes[0]}/{'-'.join(partes[1:])}" if len(partes) > 1 else ""
        with st.spinner("A ler os sites…"):
            lidos, erros = pesquisar(" ".join(partes), caminho)
        for erro in erros:
            st.warning(erro)
        db = estado()["db"]
        for anuncio in lidos:
            processar(db, dict(anuncio), BASE, referencia=True)  # enriquece o mercado
        resultado = {a["url"]: analisar(identificar(db, dict(a)), db) for a in lidos if a["preco"]}
        resultado = sorted(resultado.values(), key=lambda x: -(x["margem"] if x["margem"] is not None else -10**9))
        st.caption(f"{len(lidos)} anúncios lidos · {sum(1 for x in resultado if x['bom'])} negócios")
        grelha(resultado[:24], "pesq")
