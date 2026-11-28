"""Os sites lidos. Cada um tem o seu módulo: listar (página N) e, quando a lista não chega, completar o anúncio."""

from .. import avaliacao as av
from ..texto import ano_do_texto, km_do_texto
from .autopt import autopt, completar_autopt
from .autox import autox
from .custojusto import completar_custojusto, custojusto
from .facebook import completar_facebook, facebook
from .olx import olx
from .standvirtual import completar_standvirtual, standvirtual

# fontes lidas página a página (o Facebook é por cidade e faixa de preço: ver vigia.ciclo_facebook)
PAGINADAS = {"standvirtual": standvirtual, "olx": olx, "custojusto": custojusto,
             "autopt": autopt, "autox": autox}

COMPLETAR = {"standvirtual": completar_standvirtual, "autopt": completar_autopt,
             "custojusto": completar_custojusto, "facebook": completar_facebook}

__all__ = ["PAGINADAS", "COMPLETAR", "detalhe", "autopt", "autox", "custojusto", "facebook", "olx", "standvirtual"]


def detalhe(anuncio):
    """Lê a página do anúncio (descrição completa e características) para avaliar a fundo."""
    completar = COMPLETAR.get(anuncio["origem"])
    try:
        if completar:
            completar(anuncio)
    except (RuntimeError, StopIteration, KeyError, ValueError):
        pass  # sem detalhe, avalia com o que a lista trouxe
    # o que os campos não trouxeram, procura-se na descrição ("158 mil km", "ano 2012")
    anuncio["km"] = anuncio["km"] or km_do_texto(anuncio["texto"])
    anuncio["ano"] = anuncio["ano"] or ano_do_texto(anuncio["texto"])
    anuncio["sinais"] = av.sinais_do_texto(anuncio["titulo"], anuncio["texto"])
    anuncio.setdefault("extras", {})
    return anuncio
