"""Alerta Carros — os melhores negócios de carros em Portugal, com margem estimada, em tempo real."""

import threading
import time

import pandas as pd
import streamlit as st

import monitor as m

st.set_page_config(page_title="Alerta Carros", layout="wide")
st.markdown("""
<style>
  .hero h1 {font-size: 2.4rem; font-weight: 800; margin: 0}
  .hero h1 span {color: #e3242b}
  .hero p {color: #a3a3a3; margin: .2rem 0 1rem}
  .marca {font-weight: 700; color: #fff; margin: .5rem 0 0}
  .titulo {color: #a3a3a3; font-size: .85rem; height: 2.5em; overflow: hidden; margin: 0 0 .3rem}
  .preco {font-size: 1.5rem; font-weight: 800; margin: 0; color: #e3242b}
  .mercado {color: #a3a3a3; font-size: .8rem; margin: 0}
  .margem {color: #22c55e; font-weight: 700; margin: .1rem 0 .3rem}
  .margem.neg {color: #f87171}
  .selo {display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .75rem;
         background: #e3242b; color: #fff}
  .selo.suspeito {background: #d97706}
  .selo.neutro {background: #333; color: #d4d4d4}
  .detalhes {color: #a3a3a3; font-size: .8rem; margin: 0 0 .4rem; min-height: 2.2em}
  div[data-testid="stImage"] img {border-radius: 10px; aspect-ratio: 4/3; object-fit: cover}
  /* sem ecrã cinzento enquanto procura */
  [data-stale="true"], .stale-element {opacity: 1 !important; transition: none !important}
  div[data-testid="stStatusWidget"] {visibility: hidden}
</style>""", unsafe_allow_html=True)

CONFIG_PATH = m.ROOT / "config.toml"
if not CONFIG_PATH.is_file():
    CONFIG_PATH = m.ROOT / "config.exemplo.toml"
BASE = m.load_config(CONFIG_PATH)
FONTES_RAPIDAS = [x for x in BASE["fontes"] if x != "facebook"]  # o Facebook é lento: fica na pesquisa
ATUALIZAR_SEGUNDOS = 60
SEM_FOTO = "https://placehold.co/640x480/1f2937/9ca3af?text=Sem+foto"
COLUNAS = ["origem", "id", "titulo", "preco", "url", "ano", "km", "local", "foto", "modelo", "combustivel",
           "caixa", "profissional", "importado", "sinais", "extras"]


# ---------- recolha partilhada por todas as sessões ----------

@st.cache_resource
def estado():
    db = m.abrir_db(m.ROOT / "painel.sqlite3", check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS favoritos_v2 (origem TEXT, id TEXT, guardado_preco INTEGER, "
               "guardado_em TEXT, PRIMARY KEY (origem, id))")
    db.commit()
    return {"db": db, "lock": threading.Lock(), "ultima": 0.0, "erros": {}, "semeado": False}


def recolher():
    s = estado()
    if not s["lock"].acquire(blocking=False):
        return  # outra sessão já está a recolher
    try:
        if not s["semeado"]:
            with st.spinner("Primeira vez: a conhecer o mercado (≈1 min)…"):
                m.semear(s["db"], {**BASE, "paginas_semente": 8}, aviso=lambda *_: None)
            s["semeado"] = True
        if time.time() - s["ultima"] < ATUALIZAR_SEGUNDOS - 5:
            return
        for origem in FONTES_RAPIDAS:
            try:
                m.ciclo_paginado(s["db"], origem, BASE, so_guardar=True)
                s["erros"].pop(origem, None)
            except (RuntimeError, KeyError, TypeError, ValueError) as erro:
                s["erros"][origem] = str(erro)
        m.explorar(s["db"], BASE, aviso=lambda *_: None)  # o mercado conhecido cresce a cada minuto
        s["ultima"] = time.time()
    finally:
        s["lock"].release()


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


with st.sidebar:
    st.header("Filtros")
    f = {}
    f["preco"] = st.slider("Preço (€)", 300, 60000, (max(300, BASE["preco_min"]), BASE["preco_max"]), 100)
    f["ano"] = st.slider("Ano", 1995, 2026, (BASE["ano_min"], 2026))
    f["km"] = st.slider("Km máximos", 10000, 400000, BASE["km_max"], 10000)
    marcas = st.multiselect("Marca", lista(
        "SELECT substr(modelo, 1, instr(modelo, ' ') - 1) AS marca FROM anuncios WHERE modelo IS NOT NULL "
        "GROUP BY marca ORDER BY COUNT(*) DESC"), format_func=str.title, placeholder="Todas")
    todos_modelos = lista("SELECT modelo FROM anuncios WHERE modelo IS NOT NULL GROUP BY modelo "
                          "ORDER BY COUNT(*) DESC")
    modelos = st.multiselect("Modelo", [x for x in todos_modelos if not marcas or x.split()[0] in marcas],
                             format_func=str.title, placeholder="Todos")
    f["combustivel"] = st.multiselect("Combustível", ["Gasolina", "Gasóleo", "Híbrido", "Elétrico", "GPL"],
                                      placeholder="Todos")
    f["caixa"] = st.multiselect("Caixa", ["Manual", "Automática"], placeholder="Todas")
    f["vendedor"] = st.radio("Vendedor", ["Todos", "Particular", "Profissional"], horizontal=True)
    f["sites"] = st.multiselect("Sites", FONTES_RAPIDAS + ["facebook"], format_func=m.NOMES.get,
                                placeholder="Todos")
    f["local"] = st.text_input("Zona", placeholder="ex.: Lisboa, Porto, Braga")
    f["nacionais"] = st.checkbox("Só carros nacionais", BASE["apenas_nacionais"])
    st.header("Negócio")
    f["custos"] = st.number_input("Custos de revenda (€)", 0, 10000, BASE["custos_revenda"], 100,
                                  help="Inspeção, limpeza, pequenas reparações e anúncio")
    f["margem"] = st.number_input("Margem mínima (€)", 0, 50000, BASE["margem_minima"], 100)
    f["desconto"] = st.slider("Desconto mínimo face ao mercado (%)", 0, 60,
                              round((1 - BASE["desconto_mercado"]) * 100))
    f["comparaveis"] = st.slider("Anúncios comparáveis mínimos", 3, 30, BASE["min_comparaveis"],
                                 help="Mais anúncios = valor de mercado mais fiável")
    f["pontuacao"] = st.slider("Pontuação mínima", 0, 100, BASE["pontuacao_minima"],
                               help="Avaliação 0-100: preço vs mercado, km, descrição, donos, garantia, extras")
    f["excluir"] = st.toggle("Esconder peças, avariados e sinistrados", True)


def candidatos(limite=3000):
    """Anúncios que passam os filtros de base (SQL), mais recentes primeiro."""
    sql = [f"SELECT {', '.join(COLUNAS)} FROM anuncios WHERE modelo IS NOT NULL AND preco BETWEEN ? AND ? "
           "AND (ano IS NULL OR ano BETWEEN ? AND ?) AND (km IS NULL OR km <= ?)"]
    args = [*f["preco"], *f["ano"], f["km"]]
    for coluna, valores in (("modelo", modelos), ("combustivel", f["combustivel"]), ("caixa", f["caixa"]),
                            ("origem", f["sites"])):
        if valores:
            sql.append(f"AND {coluna} IN ({','.join('?' * len(valores))})")
            args += valores
    if marcas:
        sql.append("AND (" + " OR ".join("modelo LIKE ?" for _ in marcas) + ")")
        args += [f"{marca} %" for marca in marcas]
    if f["vendedor"] != "Todos":
        sql.append("AND profissional = ?")
        args.append(int(f["vendedor"] == "Profissional"))
    if f["local"].strip():
        sql.append("AND local LIKE ?")
        args.append(f"%{f['local'].strip()}%")
    if f["nacionais"]:
        sql.append("AND (importado IS NULL OR importado = 0)")
    sql.append("ORDER BY visto_em DESC LIMIT ?")
    args.append(limite)
    regras = {**BASE, "excluir": BASE["excluir"] if f["excluir"] else [],
              "excluir_texto": BASE["excluir_texto"] if f["excluir"] else []}
    anuncios = (m.carro(**dict(zip(COLUNAS, r))) for r in estado()["db"].execute(" ".join(sql), args))
    return [a for a in anuncios if not m.excluido(a, regras)]


def analisar(anuncio, db):
    info = m.comparar(db, anuncio, f["comparaveis"])
    sinais = m.av.carregar(anuncio.get("sinais")) or m.av.sinais_do_texto(anuncio["titulo"], anuncio["texto"])
    extras = m.av.carregar(anuncio.get("extras"))
    if info["mediana"] is None:
        return {**anuncio, "mediana": None, "n": info["n"], "margem": None, "desconto": None, "bom": False,
                "suspeito": False, "pontuacao": None, "fortes": [], "alertas": []}
    mediana = info["mediana"]
    analise = {"mediana": round(mediana), "n": info["n"], "km_mediano": info["km_mediano"],
               "margem": round(mediana - anuncio["preco"] - f["custos"]),
               "desconto": round((1 - anuncio["preco"] / mediana) * 100),
               "suspeito": anuncio["preco"] < mediana * BASE["limite_burla"]}
    pontuacao, fortes, alertas = m.av.pontuar(anuncio, analise, sinais, extras)
    return {**anuncio, **analise, "pontuacao": pontuacao, "fortes": fortes, "alertas": alertas,
            "bom": (analise["margem"] >= f["margem"] and analise["desconto"] >= f["desconto"]
                    and pontuacao >= f["pontuacao"] and not sinais["graves"])}


# ---------- cartões ----------

euros = m.euros


def cartao(anuncio, chave, favorito=False):
    with st.container(border=True):
        st.image(anuncio["foto"] or SEM_FOTO, use_container_width=True)
        if anuncio["margem"] is None:
            selo = '<span class="selo neutro">a recolher dados de mercado</span>'
        elif anuncio["suspeito"]:
            selo = f'<span class="selo suspeito">{anuncio["desconto"]}% abaixo · confirmar</span>'
        elif anuncio["bom"]:
            selo = f'<span class="selo">{anuncio["desconto"]}% abaixo do mercado</span>'
        else:
            selo = f'<span class="selo neutro">{-anuncio["desconto"]:+d}% vs mercado</span>'
        if anuncio["margem"] is None:
            margem = '<p class="margem">&nbsp;</p>'
        else:
            sinal = "+" if anuncio["margem"] >= 0 else "−"
            classe = "margem" if anuncio["margem"] >= 0 else "margem neg"
            margem = f'<p class="{classe}">margem {sinal}{euros(abs(anuncio["margem"]))}</p>'
        mercado = (f'<p class="mercado">mercado ~{euros(anuncio["mediana"])} · {anuncio["n"]} anúncios</p>'
                   if anuncio["mediana"] else '<p class="mercado">&nbsp;</p>')
        modelo = (anuncio.get("modelo") or anuncio["titulo"]).title()
        st.markdown(f'{selo}<p class="marca">{modelo}</p><p class="titulo">{anuncio["titulo"][:90]}</p>'
                    f'<p class="preco">{euros(anuncio["preco"])}</p>{mercado}{margem}',
                    unsafe_allow_html=True)
        detalhes = [str(anuncio["ano"]) if anuncio["ano"] else None,
                    f"{anuncio['km']:,} km".replace(",", " ") if anuncio["km"] else None,
                    anuncio["combustivel"], anuncio["caixa"],
                    (anuncio["local"] or "").split(",")[0] or None, m.NOMES[anuncio["origem"]]]
        st.markdown(f'<p class="detalhes">{" · ".join(d for d in detalhes if d)}</p>', unsafe_allow_html=True)
        if anuncio.get("pontuacao") is not None:
            st.markdown(f'<p class="detalhes"><b>Pontuação {anuncio["pontuacao"]}/100</b></p>',
                        unsafe_allow_html=True)
            if anuncio["fortes"]:
                st.caption("A favor: " + ", ".join(anuncio["fortes"][:4]))
            if anuncio["alertas"]:
                st.caption("Atenção: " + ", ".join(anuncio["alertas"][:3]))
        a, b = st.columns([3, 2])
        a.link_button("Ver anúncio", anuncio["url"], use_container_width=True)
        db = estado()["db"]
        if favorito:
            if b.button("Remover", key=f"{chave}-rm", help="Remover dos favoritos", use_container_width=True):
                with db:
                    db.execute("DELETE FROM favoritos_v2 WHERE origem = ? AND id = ?", (anuncio["origem"], anuncio["id"]))
                st.rerun()
        elif b.button("Guardar", key=f"{chave}-fav", help="Guardar nos favoritos", use_container_width=True):
            with db:
                db.execute("INSERT OR REPLACE INTO favoritos_v2 VALUES (?, ?, ?, datetime('now'))",
                           (anuncio["origem"], anuncio["id"], anuncio["preco"]))
            st.toast(f"Guardado nos favoritos: {anuncio['titulo'][:40]}")


def grelha(anuncios, prefixo, colunas=4, favorito=False):
    for inicio in range(0, len(anuncios), colunas):
        for coluna, anuncio in zip(st.columns(colunas), anuncios[inicio:inicio + colunas]):
            with coluna:
                cartao(anuncio, f"{prefixo}-{anuncio['origem']}-{anuncio['id']}", favorito)


# ---------- página ----------

st.markdown('<div class="hero"><h1>Alerta <span>Carros</span></h1><p>Os carros mais baratos face ao valor de '
            'mercado em Standvirtual, OLX, CustoJusto, Auto.pt e AutoX, com margem de lucro estimada e atualização '
            'a cada minuto.</p></div>', unsafe_allow_html=True)

negocios, favoritos, mercado_tab, procura = st.tabs(
    ["Melhores negócios", "Favoritos", "Mercado", "Pesquisar modelo"])


@st.fragment(run_every=ATUALIZAR_SEGUNDOS)
def painel():
    recolher()
    s = estado()
    analisados = [analisar(a, s["db"]) for a in candidatos()]

    a, b, c, d = st.columns(4)
    total = s["db"].execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    a.metric("Carros conhecidos", f"{total:,}".replace(",", " "))
    b.metric("Negócios", sum(1 for a in analisados if a["bom"]))
    margens = [a["margem"] for a in analisados if a["bom"]]
    c.metric("Melhor margem", euros(max(margens)) if margens else "—")
    d.metric("Atualizado às", time.strftime("%H:%M:%S", time.localtime(s["ultima"])) if s["ultima"] else "—")
    for origem, erro in s["erros"].items():
        st.warning(f"{m.NOMES[origem]}: {erro}")
    o1, o2 = st.columns([2, 3], vertical_alignment="bottom")
    ordem = o1.selectbox("Ordenar por", list(ORDENS), key="ordem")
    so_negocios = o2.toggle("Só negócios (margem e desconto mínimos)", True, key="so_negocios")
    escolhidos = [a for a in analisados if a["bom"]] if so_negocios else analisados
    bons = sorted(escolhidos, key=ORDENS[ordem])  # sort estável: "mais recente" mantém a ordem da base

    if not bons:
        st.info("Ainda nenhum carro com esta margem. O mercado conhecido cresce a cada minuto; "
                "experimenta baixar a margem ou o desconto na barra lateral.")
    else:
        if any(x["suspeito"] for x in bons):
            st.warning("Preços a menos de metade do mercado: confirma documentos e nunca pagues sinal "
                       "antes de ver o carro.")
        grelha(bons[:24], "bons")
        with st.expander(f"Ver os {len(bons)} negócios em tabela"):
            st.dataframe(
                pd.DataFrame(bons)[["margem", "desconto", "preco", "mediana", "n", "modelo", "titulo", "ano", "km",
                                    "combustivel", "local", "origem", "url"]],
                hide_index=True, use_container_width=True,
                column_config={"url": st.column_config.LinkColumn("anúncio", display_text="abrir"),
                               "margem": st.column_config.NumberColumn("margem", format="%d €"),
                               "preco": st.column_config.NumberColumn("preço", format="%d €"),
                               "mediana": st.column_config.NumberColumn("mercado", format="%d €"),
                               "desconto": st.column_config.NumberColumn("desconto", format="%d %%"),
                               "n": st.column_config.NumberColumn("comparáveis"),
                               "ano": st.column_config.NumberColumn(format="%d")})

    st.subheader("Acabados de chegar")
    grelha(analisados[:8], "novos")


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
    anuncios = [{**analisar(m.carro(**dict(zip(COLUNAS, r[:-1]))), db), "guardado": r[-1]} for r in rows]
    baixaram = [x for x in anuncios if x["preco"] < x["guardado"]]
    a, b, c = st.columns(3)
    a.metric("Favoritos", len(anuncios))
    b.metric("Baixaram de preço", len(baixaram))
    c.metric("Margem total estimada", euros(sum(x["margem"] or 0 for x in anuncios)))
    for x in baixaram:
        st.success(f"Baixou de preço: {x['titulo'][:60]}: {euros(x['guardado'])} → {euros(x['preco'])}")
    grelha(anuncios, "fav", favorito=True)


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
        x.dataframe(por_ano, hide_index=True, use_container_width=True,
                    column_config={**{c: st.column_config.NumberColumn(format="%d €")
                                      for c in ("mediana", "minimo", "maximo")},
                                   "ano": st.column_config.NumberColumn(format="%d"),
                                   "km_mediano": st.column_config.NumberColumn("km mediano", format="%d")})
        y.markdown(f"**{escolha.title()} — preço mediano por ano**")
        y.line_chart(por_ano, x="ano", y="mediana", y_label="€", x_label="ano")
    st.markdown("**Modelos com mais anúncios**")
    st.dataframe(resumo.head(100), hide_index=True, use_container_width=True,
                 column_config={"preco_medio": st.column_config.NumberColumn("preço médio", format="%d €")})


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
    st.caption("Lê agora o OLX, o Standvirtual e o Facebook para um modelo à tua escolha e junta-os ao mercado.")
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    texto = c1.text_input("Marca e modelo", "Toyota Yaris")
    if c2.button("Pesquisar", type="primary", use_container_width=True) and texto.strip():
        st.session_state["pesquisa"] = texto.strip()
    if st.session_state.get("pesquisa"):
        partes = m.words(st.session_state["pesquisa"]).split()
        caminho = f"{partes[0]}/{'-'.join(partes[1:])}" if len(partes) > 1 else ""
        with st.spinner("A ler os sites…"):
            lidos, erros = pesquisar(" ".join(partes), caminho)
        for erro in erros:
            st.warning(erro)
        db = estado()["db"]
        for anuncio in lidos:
            m.processar(db, dict(anuncio), BASE, referencia=True)  # enriquece o mercado
        resultado = {a["url"]: analisar({**a, "modelo": m.modelo_de(a["titulo"])}, db) for a in lidos if a["preco"]}
        resultado = sorted(resultado.values(), key=lambda x: -(x["margem"] if x["margem"] is not None else -10**9))
        st.caption(f"{len(lidos)} anúncios lidos · {sum(1 for x in resultado if x['bom'])} negócios")
        grelha(resultado[:24], "pesq")
