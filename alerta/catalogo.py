"""Marca e modelo com certeza, sem listas escritas à mão.

1. O Standvirtual e o OLX (mesmo grupo, mesma nomenclatura) dão a marca e o modelo em campos próprios:
   esses valores são a referência ("site").
2. O catálogo é o conjunto de pares marca/modelo que esses dois sites usam, aprendido da própria base.
3. Os outros anúncios (CustoJusto, Facebook, AutoX, Auto.pt) só recebem um modelo se o texto contiver
   exatamente uma marca e um modelo do catálogo ("catalogo"). Na dúvida, o modelo fica desconhecido (None):
   o carro aparece no painel, mas não entra no valor de mercado nem dá alerta.
"""

import time
from collections import Counter

from .texto import words

FONTES_REFERENCIA = ("standvirtual", "olx")  # dão marca e modelo em campos, com a mesma nomenclatura
CACHE_SEGUNDOS = 600
_CACHE = {}  # caminho da base -> (quando, Catalogo)


def slug(valor):
    """"Mercedes-Benz" -> "mercedes-benz"; "Classe A" -> "classe-a" (a forma dos endereços do Standvirtual/OLX)."""
    return "-".join(words(valor or "").split()) or None


def chave(marca, modelo):
    marca, modelo = slug(marca), slug(modelo)
    return f"{marca} {modelo}" if marca and modelo else None


def _posicao(tokens, seq, sufixo=False):
    """seq aparece seguida em tokens? sufixo: o último aceita uma letra a mais quando acaba em número
    ("320" casa com "320d"/"320i", "c3" não casa com "c30")."""
    n = len(seq)
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n - 1] != seq[:-1]:
            continue
        ultimo, alvo = tokens[i + n - 1], seq[-1]
        if ultimo == alvo or (sufixo and alvo[-1].isdigit() and len(ultimo) == len(alvo) + 1
                              and ultimo.startswith(alvo) and ultimo[-1].isalpha()):
            return True
    return False


class Catalogo:
    def __init__(self, chaves):
        self.modelos = {}  # marca -> [(palavras do modelo, modelo)]
        for k in chaves:
            marca, modelo = k.split(" ", 1)
            self.modelos.setdefault(marca, []).append((modelo.split("-"), modelo))
        # a marca escreve-se pelo nome completo ("land-rover") ou pela 1.ª palavra, se nenhuma outra começar igual
        primeiras = Counter(m.split("-")[0] for m in self.modelos)
        self.marcas = [(m.split("-"), m) for m in self.modelos]
        self.marcas += [([m.split("-")[0]], m) for m in self.modelos
                        if "-" in m and primeiras[m.split("-")[0]] == 1]

    def marca(self, texto):
        tokens = words(texto).split()
        achadas = {m for seq, m in self.marcas if _posicao(tokens, seq)}
        return achadas.pop() if len(achadas) == 1 else None

    def modelo(self, texto, marca):
        """O modelo do catálogo dessa marca que aparece no texto; o mais específico ganha; empate = None."""
        tokens = words(texto).split()
        achados = [(len(seq), m) for seq, m in self.modelos.get(marca, []) if _posicao(tokens, seq, sufixo=True)]
        if not achados:
            return None
        maior = max(n for n, _ in achados)
        melhores = {m for n, m in achados if n == maior}
        return f"{marca} {melhores.pop()}" if len(melhores) == 1 else None


def catalogo(db):
    """Catálogo aprendido da base (anúncios do Standvirtual/OLX com marca e modelo do site), em cache."""
    base = db.execute("PRAGMA database_list").fetchone()[2]
    guardado = _CACHE.get(base) if base else None
    if guardado and time.monotonic() - guardado[0] < CACHE_SEGUNDOS:
        return guardado[1]
    cat = Catalogo(r[0] for r in db.execute(
        "SELECT DISTINCT modelo FROM anuncios WHERE modelo_fonte = 'site' AND modelo IS NOT NULL"))
    if base:
        _CACHE[base] = (time.monotonic(), cat)
    return cat


def identificar(db, anuncio, cat=None):
    """Preenche anuncio["marca"], ["modelo"] e ["modelo_fonte"] ("site", "catalogo" ou None)."""
    if anuncio["origem"] in FONTES_REFERENCIA and chave(anuncio.get("marca"), anuncio.get("modelo_site")):
        anuncio["marca"] = slug(anuncio["marca"])
        anuncio["modelo"], anuncio["modelo_fonte"] = chave(anuncio["marca"], anuncio["modelo_site"]), "site"
        return anuncio
    cat = cat or catalogo(db)
    texto = f"{anuncio.get('modelo_site') or ''} {anuncio['titulo']}"
    marca = slug(anuncio.get("marca"))
    if marca not in cat.modelos:  # marca do site que o catálogo não conhece (ainda): procura no texto
        marca = cat.marca(f"{anuncio.get('marca') or ''} {anuncio['titulo']}")
    modelo = cat.modelo(texto, marca) if marca else None
    anuncio["marca"] = marca or slug(anuncio.get("marca"))
    anuncio["modelo"], anuncio["modelo_fonte"] = (modelo, "catalogo") if modelo else (None, None)
    return anuncio
