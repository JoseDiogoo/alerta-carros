# Alerta Carros

Encontra os carros mais baratos face ao **valor de mercado** em Portugal, estima a **margem de lucro** e avisa no Telegram, sem serviços pagos.

Lê diretamente o **Standvirtual, OLX, CustoJusto, Auto.pt, AutoX e Facebook Marketplace**, todos em paralelo: cada site de 15 em 15 segundos e o Facebook (7 cidades) de 3 em 3 minutos. Não há listas de marcas ou modelos escritas à mão. O Standvirtual e o OLX dão a marca e o modelo em campos próprios, e esses valores formam um catálogo que se aprende sozinho. Nos outros sites (CustoJusto, Facebook, AutoX, Auto.pt), o modelo só é aceite quando o texto do anúncio contém exatamente uma marca e um modelo desse catálogo. Na dúvida, o carro aparece no painel como "modelo por confirmar", mas não entra no valor de mercado nem dá alerta.

## Como é avaliado cada carro

1. **Comparáveis rigorosos.** O carro é comparado com o mesmo modelo e combustível, ano ±2 e km parecidos. Primeiro procura carros com a mesma cilindrada, potência e caixa, e só alarga a comparação se houver poucos. O mesmo carro publicado em vários sites conta uma só vez.
2. **Preços ajustados.** O preço de cada comparável é ajustado à diferença de ano e de km. A desvalorização por ano e por km é aprendida dos anúncios de cada modelo.
3. **Preços absurdos removidos** (peças, erros), seguidos da mediana.
4. **Valor prudente.** Quando a comparação é fraca (sem km, poucos carros iguais, preços muito variáveis), usa-se um valor mais baixo do mercado (o percentil 30) em vez da mediana. Se o mercado for demasiado disperso, não há alerta.
5. **Página completa.** Nos candidatos a negócio é lida a página do anúncio: descrição, km, combustível, caixa, donos, garantia e 2.ª chave.
6. **Avarias.** A descrição é lida e as avarias mencionadas (embraiagem, turbo, motor, chapa…) aparecem com a etiqueta **⚠ AVARIA**. O custo da reparação **não é estimado** (só um mecânico o sabe) e a avaria não tira pontos: a margem é a do carro como se estivesse bom. Só são excluídos os carros para peças, salvados, com penhora, sem documentos ou com matrícula estrangeira.

**Margem = valor de mercado × 0,95 (venda provável) − preço − custos de revenda.**

## O que vai para o Telegram

O painel mostra **todos** os carros dentro dos filtros (até 5 000 €). O Telegram só recebe os melhores:

- preço **pelo menos 30% abaixo** do valor de mercado (**15%** nas `marcas_foco`, por defeito Toyota);
- valor de mercado tirado de **pelo menos 10 carros iguais**, com preços pouco dispersos;
- **pontuação ≥ 75** (preço face ao mercado, km, descrição, donos, garantia…);
- os carros com avaria também podem ir, com "⚠️ AVARIA: …" na mensagem.

## Ficheiros

O código está no pacote `alerta/`, organizado por camadas (de dentro para fora):

| Módulo | O que faz |
|---|---|
| `alerta/texto.py`, `alerta/anuncio.py` | Dados de um anúncio: normalização, km, ano, motor. Sem rede nem base. |
| `alerta/catalogo.py` | Marca e modelo com certeza: campos do site ou catálogo aprendido. Sem listas escritas à mão. |
| `alerta/avaliacao.py` | Lê a descrição (avarias, pontos fortes) e dá a pontuação 0-100. |
| `alerta/mercado.py` | Valor de mercado a partir de carros iguais, com os preços ajustados a ano e km. |
| `alerta/negocio.py` | Filtros do utilizador e critério do Telegram. |
| `alerta/fontes/` | Um módulo por site (`standvirtual`, `olx`, `custojusto`, `autopt`, `autox`, `facebook`) e o pedido HTTP. |
| `alerta/db.py` | Base SQLite: esquema, gravação e regras de limpeza. |
| `alerta/telegram.py` | Envio e texto das mensagens. |
| `alerta/vigia.py` | Junta tudo: lê cada site ao seu ritmo, guarda, avalia e alerta. |
| `alerta/config.py` | `config.toml` e segredos. |

- `monitor.py` é a linha de comandos: vigia os 6 sites e envia os bons negócios para o Telegram, com foto, preço, motor, km, margem, avarias mencionadas, valor de mercado e link.
- `app.py` é o painel Streamlit com os separadores **Melhores negócios** (todos os carros, ordenados pela pontuação, com a opção de ver só os do critério do Telegram), **Toyota** (prontos a vender e com avaria), **Favoritos**, **Mercado** e **Pesquisar modelo**. Atualiza-se a cada 15 segundos, com a recolha a correr em segundo plano.
- `config.toml` guarda os critérios. Se não existir, é usado o `config.exemplo.toml`. `modelos = []` significa todos os carros. `desconto_telegram`, `desconto_foco`, `comparaveis_telegram` e `pontuacao_minima` definem o critério do Telegram.

## Regras da base de dados

- Só se guardam anúncios **até 15 000 €** (`preco_max_guardar`). Acima disso, um anúncio nunca serve de comparação para um carro até 5 000 €. Do Facebook, que mistura carros, motas e acessórios, só se guardam os que têm modelo confirmado.
- Cada anúncio guarda a origem do modelo (`modelo_fonte`): `site` (campos do Standvirtual/OLX), `catalogo` (correspondência exata) ou vazio. De 10 em 10 minutos, os anúncios sem modelo voltam a ser comparados com o catálogo, que entretanto cresceu.
- Um anúncio que **não aparece nos sites há 60 dias** (`retencao_dias`) é apagado, porque já foi vendido ou retirado. Os favoritos nunca são apagados.
- A limpeza corre no arranque e de 6 em 6 horas. Quando apaga mais de 1 000 anúncios, a base é compactada.
- Há índices para os comparáveis (`modelo, ano`), para os mais recentes (`visto_em`) e para a limpeza (`atualizado_em`).

Os pormenores técnicos de cada fonte estão em [PESQUISA.md](PESQUISA.md).

## Painel

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## Alertas no Telegram (no PC, opcional)

O bot é o @alerta_carros_pt_bot e o grupo é "Alerta Carros". Os segredos ficam em `.streamlit/secrets.toml`, que não vai para o Git:

```toml
TELEGRAM_BOT_TOKEN = "o token do @BotFather"
TELEGRAM_CHAT_ID = "-5586087955"
```

```powershell
python monitor.py --test-telegram   # a mensagem de teste deve chegar ao grupo
.\iniciar_monitor.bat               # vigia sem parar e reinicia sozinho se cair
```


Na primeira execução, o monitor guarda os anúncios existentes como referência e não envia nada. A partir daí, cada anúncio novo, ou cada descida de preço de pelo menos 5%, que cumpra os critérios dá um alerta. Se o envio falhar, o anúncio volta a ser tentado no ciclo seguinte. O mesmo carro publicado em dois sites só gera um alerta. Se um site bloquear ou falhar, o monitor espera cada vez mais tempo antes de lá voltar (até 10 minutos), sem afetar os outros sites.

## Alertas 24/7 na nuvem (com o PC desligado)

O workflow do GitHub Actions (`.github/workflows/alertas.yml`) vigia os sites de 15 em 15 segundos durante cerca de 5h40 (`python monitor.py --minutos 340`). No fim, guarda o mercado aprendido na cache e relança-se a si próprio, sem precisar de token. Se a cadeia se quebrar, o agendamento de hora a hora retoma-a. O repositório é público, por isso os minutos do GitHub Actions são ilimitados. O Facebook é lido pela categoria de veículos, sem login, mas bloqueia os servidores do GitHub (redireciona para o login). Na nuvem fica de fora, e só funciona a partir de uma ligação de casa.

Para parar os alertas: GitHub → Actions → "Alertas de carros" → "Disable workflow".

Se o monitor também estiver a correr no PC ao mesmo tempo, o mesmo carro pode dar dois alertas.

## Testes

```powershell
python -B -m unittest test_monitor -v
python -m pyflakes monitor.py app.py alerta/   # verificação estática (pip install pyflakes)
```

## Aviso

A recolha automática pode violar os termos de utilização destes sites. Usa isto só para fins pessoais e com moderação. Antes de pagar, confirma sempre o carro e os documentos ao vivo, e leva um mecânico para confirmar o custo das reparações. Desconfia de preços muito abaixo do mercado e de pedidos de sinal.
