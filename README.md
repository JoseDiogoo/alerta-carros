# 🚗 Alerta Carros

Encontra carros **bons e baratos** em Portugal e avisa no Telegram, sem serviços pagos.

Lê diretamente o **Standvirtual, OLX, CustoJusto, Auto.pt, AutoX e Facebook Marketplace** e compara cada anúncio com a **mediana do mercado** (mesmo modelo, ano ±1, em todos os sites).

- `monitor.py` corre no PC, verifica os sites de minuto a minuto e envia os alertas para o Telegram, com foto, preço, ano, km, local, desconto face ao mercado e link.
- `app.py` é o painel Streamlit. Tem os negócios do momento em cartões, os ⭐ favoritos e a pesquisa por modelo, e atualiza-se sozinho.
- `config.toml` define o que conta como "bom e barato": preço, ano, km, km por ano, desconto mínimo, exclusões e modelos. Para começar, copia `config.exemplo.toml`.

Os pormenores técnicos de cada fonte (frescura, campos e cobertura) estão em [PESQUISA.md](PESQUISA.md).

## Painel

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Na primeira abertura, o painel junta preços de referência durante cerca de 2 minutos. Depois atualiza-se sozinho a cada minuto.

## Alertas no Telegram (24/7 no PC)

1. No @BotFather, cria um bot com `/newbot` e guarda o token.
2. Cria um grupo com quem deve receber os alertas, adiciona o bot e escreve uma mensagem no grupo.
3. No PowerShell, na pasta do projeto:

```powershell
Copy-Item config.exemplo.toml config.toml
$env:TELEGRAM_BOT_TOKEN = [System.Net.NetworkCredential]::new('', (Read-Host 'Token do bot' -AsSecureString)).Password
python monitor.py --chat-id          # mostra o ID do grupo (número negativo)
$env:TELEGRAM_CHAT_ID = Read-Host 'ID do grupo'
python monitor.py --test-telegram
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
