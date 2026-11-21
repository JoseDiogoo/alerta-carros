"""O anúncio: estrutura comum a todos os sites."""

from .texto import cilindrada_do_texto, combustivel_do_titulo, potencia_do_texto


NOMES = {"standvirtual": "Standvirtual", "olx": "OLX", "custojusto": "CustoJusto",
         "autopt": "Auto.pt", "autox": "AutoX", "facebook": "Facebook Marketplace"}


AVALIACAO_SITE = {"BELOW": "abaixo do mercado", "IN": "dentro do mercado", "ABOVE": "acima do mercado"}


def carro(origem, id, titulo, preco, url, **extra):
    return {"origem": origem, "id": str(id), "titulo": (titulo or "").strip(), "preco": preco,
            "url": url, "km": None, "ano": None, "local": "", "profissional": None,
            "importado": None, "avaliacao_site": None, "texto": "", "destaque": False, "foto": None,
            "combustivel": None, "caixa": None, "cilindrada": None, "potencia": None, "carrocaria": None, **extra}


def completar_motor(anuncio):
    """Cilindrada, potência e combustível pelo título quando o site não os dá em campos próprios."""
    anuncio["combustivel"] = anuncio.get("combustivel") or combustivel_do_titulo(anuncio["titulo"])
    anuncio["cilindrada"] = anuncio.get("cilindrada") or cilindrada_do_texto(anuncio["titulo"])
    anuncio["potencia"] = anuncio.get("potencia") or potencia_do_texto(
        f"{anuncio['titulo']} {(anuncio.get('texto') or '')[:300]}")
