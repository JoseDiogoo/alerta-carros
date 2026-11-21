# Alerta Carros

Encontra os carros mais baratos face ao **valor de mercado** em Portugal, estima a **margem de lucro** e avisa no Telegram, sem serviços pagos.

Lê diretamente o **Standvirtual, OLX, CustoJusto, Auto.pt, AutoX e Facebook Marketplace**, todos em paralelo: cada site de 15 em 15 segundos e o Facebook (7 cidades) de 3 em 3 minutos. Não há lista fixa de carros: o modelo é detetado no título (marca + modelo, com as versões desportivas à parte e erros de escrita corrigidos).

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

- `monitor.py` corre no PC, vigia os 6 sites e envia os bons negócios para o Telegram, com foto, preço, motor, km, margem, avarias mencionadas, valor de mercado e link. Também lê aos poucos páginas mais antigas, sobretudo até ao dobro do preço máximo e das marcas em foco, para conhecer o mercado.
- `app.py` é o painel Streamlit com os separadores **Melhores negócios** (todos os carros, ordenados pela pontuação, com a opção de ver só os do critério do Telegram), **Toyota** (prontos a vender e com avaria), **Favoritos**, **Mercado** e **Pesquisar modelo**. Atualiza-se a cada 15 segundos, com a recolha a correr em segundo plano.
- `config.toml` guarda os critérios. Se não existir, é usado o `config.exemplo.toml`. `modelos = []` significa todos os carros. `desconto_telegram`, `desconto_foco`, `comparaveis_telegram` e `pontuacao_minima` definem o critério do Telegram.

Os pormenores técnicos de cada fonte estão em [PESQUISA.md](PESQUISA.md).

## Painel

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## Alertas no Telegram (24/7 no PC)

O bot é o @alerta_carros_pt_bot e o grupo é "Alerta Carros". Os segredos ficam em `.streamlit/secrets.toml`, que não vai para o Git:

```toml
TELEGRAM_BOT_TOKEN = "o token do @BotFather"
TELEGRAM_CHAT_ID = "-5586087955"
```

```powershell
python monitor.py --test-telegram   # a mensagem de teste deve chegar ao grupo
.\iniciar_monitor.bat               # vigia sem parar e reinicia sozinho se cair
```

Para arrancar com o Windows, põe um atalho para o `iniciar_monitor.bat` na pasta que abre com `Win+R` → `shell:startup`. Nas opções de energia, desliga a suspensão do PC.

Na primeira execução, o monitor guarda os anúncios existentes como referência e não envia nada. A partir daí, cada anúncio novo, ou cada descida de preço de pelo menos 5%, que cumpra os critérios dá um alerta. Se o envio falhar, o anúncio volta a ser tentado no ciclo seguinte. O mesmo carro publicado em dois sites só gera um alerta. Se um site bloquear ou falhar, o monitor espera cada vez mais tempo antes de lá voltar (até 10 minutos), sem afetar os outros sites.

O workflow do GitHub Actions (`.github/workflows/alertas.yml`) corre de 30 em 30 minutos, com uma base de dados própria. Se o monitor estiver a correr no PC ao mesmo tempo, o mesmo carro pode dar dois alertas.

## Testes

```powershell
python -B -m unittest test_monitor -v
```

## Aviso

A recolha automática pode violar os termos de utilização destes sites. Usa isto só para fins pessoais e com moderação. Antes de pagar, confirma sempre o carro e os documentos ao vivo, e leva um mecânico para confirmar o custo das reparações. Desconfia de preços muito abaixo do mercado e de pedidos de sinal.
