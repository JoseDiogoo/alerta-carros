"""Alerta Carros: os carros mais baratos face ao valor de mercado em Portugal, com alertas no Telegram.

Camadas (de dentro para fora):
  texto, anuncio      -> dados de um anúncio, sem I/O
  avaliacao           -> sinais da descrição e pontuação 0-100
  mercado, negocio    -> valor de mercado e critério dos alertas
  fontes/             -> um módulo por site (HTTP)
  db, telegram        -> persistência e envio
  vigia               -> orquestra: ler, guardar, avaliar, alertar
  config              -> config.toml e segredos
"""
