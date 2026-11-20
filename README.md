# Alerta Carros

Encontra os carros mais baratos face ao **valor de mercado** em Portugal, estima a **margem de lucro** e avisa no Telegram, sem serviços pagos.

Lê diretamente o **Standvirtual, OLX, CustoJusto, Auto.pt, AutoX e Facebook Marketplace**. Não há lista fixa de carros: o modelo é detetado no título (marca + modelo, com as versões desportivas à parte) e cada anúncio é comparado com a mediana do mesmo modelo, ano ±1 e km parecidos.

**Margem estimada = valor de mercado − preço − custos de revenda.** São preços pedidos e não preços de venda, por isso a margem real tende a ser um pouco menor.

- `monitor.py` corre no PC, verifica os sites de minuto a minuto e envia os alertas para o grupo do Telegram, com foto, preço, ano, km, margem, valor de mercado e link. Também lê, aos poucos, páginas mais antigas para conhecer o mercado inteiro (cerca de 45 mil carros no Standvirtual).
- `app.py` é o painel Streamlit em modo escuro, com os melhores negócios por margem, os favoritos, os preços de mercado por modelo e ano e a pesquisa por modelo. Tem filtros de marca, modelo, combustível, caixa, preço, ano, km, vendedor, site, zona, custos, margem e desconto.
- `config.toml` guarda os critérios por defeito. `modelos = []` significa todos os carros. Para começar, copia `config.exemplo.toml`.

Os pormenores técnicos de cada fonte estão em [PESQUISA.md](PESQUISA.md).

## Painel

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Na primeira abertura, o painel junta preços de referência durante cerca de 2 minutos. Depois atualiza-se sozinho a cada minuto.

## Alertas no Telegram (24/7 no PC)

O bot é o @alerta_carros_pt_bot e o grupo é "Alerta Carros". Os segredos ficam em `.streamlit/secrets.toml`, que não vai para o Git:

```toml
TELEGRAM_BOT_TOKEN = "o token do @BotFather"
TELEGRAM_CHAT_ID = "-5586087955"
```

```powershell
Copy-Item config.exemplo.toml config.toml
python monitor.py --test-telegram   # a mensagem de teste deve chegar ao grupo
python monitor.py
```

Na primeira execução, o monitor guarda os anúncios existentes como referência e não envia nada. A partir daí, cada anúncio novo, ou cada descida de preço de pelo menos 5%, que cumpra os filtros dá um alerta. Se o envio falhar, o anúncio volta a ser tentado no ciclo seguinte. O mesmo carro publicado em dois sites só gera um alerta.

Para receber alertas em tempo real, o monitor tem de correr numa máquina com ligação de casa. O Facebook costuma bloquear os servidores na nuvem.

## Testes

```powershell
python -B -m unittest test_monitor -v
```

## Aviso

A recolha automática pode violar os termos de utilização destes sites. Usa isto só para fins pessoais e com moderação. Antes de pagar, confirma sempre o carro e os documentos ao vivo. Desconfia de preços muito abaixo do mercado e de pedidos de sinal.
