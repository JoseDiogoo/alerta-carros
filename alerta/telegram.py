"""Envio para o Telegram e texto das mensagens."""

import re
import threading
import time

from . import registo
from .anuncio import NOMES
from .config import secret
from .fontes.http import api
from .texto import euros


def telegram(method, **data):
    token = secret("TELEGRAM_BOT_TOKEN")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("O token do Telegram tem um formato inválido.")
    response = api("Telegram", f"https://api.telegram.org/bot{token}/{method}", data=data)
    if not response.get("ok"):
        raise RuntimeError("O Telegram rejeitou o pedido.")
    return response["result"]


ULTIMO_ENVIO = [0.0]


ENVIO = threading.Lock()


def send(message, foto=None):
    with ENVIO:  # as fontes correm em paralelo: o ritmo do Telegram é partilhado
        # Telegram: grupos aceitam ~20 mensagens/minuto; acima disso responde HTTP 429
        espera = ULTIMO_ENVIO[0] + 3.2 - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        ULTIMO_ENVIO[0] = time.monotonic()
        _enviar(message, foto)


def _enviar(message, foto):
    chat = secret("TELEGRAM_CHAT_ID")
    if foto and len(message) <= 1024:
        try:
            telegram("sendPhoto", chat_id=chat, photo=foto, caption=message)
            return
        except RuntimeError:
            pass  # foto recusada pelo Telegram: segue em texto
    telegram("sendMessage", chat_id=chat, text=message, link_preview_options={"is_disabled": True})


def mensagem_simples(anuncio):
    """Alerta de qualquer anúncio novo (opção alertar_todos), sem análise de negócio."""
    facts = [euros(anuncio["preco"]), str(anuncio["ano"] or ""),
             f"{anuncio['km']:,} km".replace(",", " ") if anuncio["km"] else "",
             anuncio["combustivel"] or "", (anuncio["local"] or "").split(",")[0]]
    return "\n".join([f"Novo anúncio ({NOMES[anuncio['origem']]})", anuncio["titulo"][:150],
                      " · ".join(x for x in facts if x), anuncio["url"]])


def mensagem(anuncio, analise, old_price):
    facts = [euros(anuncio["preco"])]
    if anuncio["ano"]:
        facts.append(str(anuncio["ano"]))
    if anuncio["km"]:
        facts.append(f"{anuncio['km']:,} km".replace(",", " "))
    if anuncio.get("cilindrada"):
        facts.append(f"{anuncio['cilindrada'] / 1000:.1f}")
    if anuncio.get("potencia"):
        facts.append(f"{anuncio['potencia']} cv")
    for extra in (anuncio["combustivel"], anuncio["caixa"]):
        if extra:
            facts.append(extra)
    if anuncio["profissional"] is not None:
        facts.append("profissional" if anuncio["profissional"] else "particular")
    if anuncio["importado"]:
        facts.append("importado")
    avaria = analise.get("avaria") or {}
    header = f"{anuncio['modelo'].title()} · {NOMES[anuncio['origem']]}"
    if anuncio["local"]:
        header += f" · {anuncio['local'][:60]}"
    lines = [header, anuncio["titulo"][:150], " · ".join(facts),
             f"Pontuação {analise['pontuacao']}/100",
             f"Margem estimada: +{euros(analise['margem'])} (venda provável ~{euros(analise['venda'])})",
             f"Mercado ~{euros(analise['mediana'])}{' (valor prudente)' if analise.get('prudente_usado') else ''}"
             f" · {analise['desconto']}% abaixo · "
             f"{analise['n']} carros comparáveis ({analise['nivel']})"]
    if old_price:
        lines.append(f"Baixou de {euros(old_price)}")
    minutos = registo.atraso(anuncio)
    if minutos is not None and 15 < minutos <= 7 * 24 * 60:  # chegou tarde: pode já estar vendido
        lines.append(f"Publicado há {minutos / 60:.0f} h" if minutos >= 90 else f"Publicado há {minutos:.0f} min")
    if anuncio["avaliacao_site"]:
        lines.append(f"{NOMES[anuncio['origem']]}: {anuncio['avaliacao_site']}")
    if analise.get("fortes"):
        lines.append("A favor: " + ", ".join(analise["fortes"][:5]))
    if analise.get("alertas"):
        lines.append("Atenção: " + ", ".join(analise["alertas"][:4]))
    if avaria.get("tem"):
        lines.insert(2, f"⚠️ AVARIA: {', '.join(avaria['itens'][:4])}"
                        + (" (por identificar)" if avaria["incerta"] else "")
                        + " · margem sem a reparação, confirmar com mecânico")
    if not anuncio["km"]:
        lines.insert(2, "⚠️ SEM KM no anúncio: valor de mercado prudente, confirma os km antes de avançar")
    if analise["suspeito"]:
        lines.append("ATENÇÃO: muito abaixo do mercado. Confirma documentos e não pagues sinal antes de ver o carro.")
    return "\n".join(lines + [anuncio["url"]])
