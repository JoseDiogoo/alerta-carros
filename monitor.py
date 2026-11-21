"""Alertas de carros no Telegram, lidos diretamente dos sites. Ponto de entrada da linha de comandos.

    python monitor.py                  vigia sem parar
    python monitor.py --minutos 340    vigia durante N minutos (GitHub Actions) e sai
    python monitor.py --once           uma passagem por todas as fontes
    python monitor.py --test-telegram  mensagem de teste
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from alerta.anuncio import NOMES
from alerta.config import ROOT, load_config, secret
from alerta.db import abrir_db, limpar
from alerta.telegram import send, telegram
from alerta.vigia import ciclo_facebook, ciclo_paginado, explorar, semear, vigiar_tudo


def main():
    parser = argparse.ArgumentParser(description="Alertas de carros para o Telegram")
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--chat-id", action="store_true")
    mode.add_argument("--test-telegram", action="store_true")
    parser.add_argument("--minutos", type=float, help="vigia só durante N minutos (GitHub Actions) e sai")
    args = parser.parse_args()
    if args.chat_id:
        updates = telegram("getUpdates", timeout=5, allowed_updates=["message"])
        chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("title") or "conversa privada"
                 for u in updates if "message" in u}
        if not chats:
            raise ValueError("Envia /start ao bot (ou uma mensagem no grupo) e volta a correr --chat-id.")
        for chat_id, nome in chats.items():
            print(chat_id, nome)
        return 0
    if args.test_telegram:
        send("Alerta Carros: teste de ligação ao Telegram.")
        return 0
    secret("TELEGRAM_BOT_TOKEN")
    secret("TELEGRAM_CHAT_ID")
    if not args.config.is_file() and args.config == ROOT / "config.toml":
        args.config = ROOT / "config.exemplo.toml"  # sem config.toml próprio usa os critérios de exemplo
        print("A usar config.exemplo.toml (copia-o para config.toml para mudar os critérios).", flush=True)
    config = load_config(args.config)
    # a base fica sempre em config.sqlite3 (é esse o ficheiro guardado na cache do GitHub)
    caminho = (ROOT / "config.sqlite3" if args.config.name == "config.exemplo.toml"
               else args.config.resolve().with_suffix(".sqlite3"))
    db = abrir_db(caminho)
    print(f"Limpeza da base: {limpar(db, config)} anúncios antigos ou inúteis apagados.", flush=True)
    semear(db, config)
    if args.once:  # GitHub Actions: uma passagem por todas as fontes
        failed = False
        for origem in config["fontes"]:
            try:
                total, referencia = (ciclo_facebook(db, config) if origem == "facebook"
                                     else ciclo_paginado(db, origem, config))
                print(f"{NOMES[origem]}: {total} anúncios lidos" + (" (referência inicial)" if referencia else ""),
                      flush=True)
            except (RuntimeError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
                print(f"{datetime.now():%H:%M:%S} {NOMES[origem]}: {error}", flush=True)
                failed = True
        explorar(db, config)
        db.close()  # fecha o WAL: a cache do GitHub guarda só o .sqlite3
        return int(failed)
    db.close()
    vigiar_tudo(caminho, config, args.minutos)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from None
