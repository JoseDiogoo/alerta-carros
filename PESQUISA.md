# Pesquisa: fontes grátis e em tempo real (20/11/2026)

Testes feitos a partir de casa, sem login e sem serviços pagos. Usam só a biblioteca padrão do Python (`urllib`) com os cabeçalhos `User-Agent` de Chrome, `Accept: text/html` e `Sec-Fetch-Mode: navigate`.

## Fontes

| Fonte | Frescura medida | Por pedido | Dados | Onde está o JSON |
|---|---|---|---|---|
| Standvirtual | ~3 min | 32 | preço, km, ano, combustível, caixa, cv, origem | `__NEXT_DATA__` → `urqlState[*].data` → `advertSearch.edges` |
| OLX.pt | minutos (ordena pela última atualização) | 52 | preço, km, ano, combustível, caixa, cv, origem, particular/profissional | `window.__PRERENDERED_STATE__` → `listing.listing.ads` |
| CustoJusto | ~0 min | 40 | preço, ano, combustível, caixa, descrição, particular/profissional | `__NEXT_DATA__` → `listItems` |
| Facebook Marketplace | 8–30 min com `daysSinceListed=1` | 24 | preço, cidade, data, vendido/reservado (sem km) | `<script type="application/json">` → objetos com `marketplace_listing_title` |
| AutoUncle (84 sites) | diária, não é tempo real | 25 | preço, km, ano, cv + **avaliação de preço** ("Bom preço", "Poupa € X") | JSON-LD `Vehicle` + HTML |
| AUTO.MOTO.pt | não medida | 20 | preço, km, ano, combustível, caixa, `price_rate` | `__NEXT_DATA__` → `items` |

URLs ordenados pelos mais recentes:
- Standvirtual: `https://www.standvirtual.com/carros?search[order]=created_at_first:desc`
- OLX: `https://www.olx.pt/carros-motos-e-barcos/carros/?search[order]=created_at:desc`
- CustoJusto: `https://www.custojusto.pt/portugal/veiculos/carros-usados`
- Facebook: `https://www.facebook.com/marketplace/lisbon/search/?query=carro&daysSinceListed=1&minPrice=1000&maxPrice=3000`
- AutoUncle: `https://www.autouncle.pt/pt/carros-usados/Renault/Clio?s[order_by]=created_at_desc` (também aceita `recently_price_dropped` e `au_rating_desc`)

## Cobertura

- **OLX:** cada pesquisa devolve no máximo 1000 resultados. Para ter mais, é preciso dividir por faixas de preço ou por região.
- **Facebook sem login:** a paginação e a ordenação por data não funcionam. Para cobrir mais, divide-se por faixa de preço (4 faixas dão 95 anúncios únicos contra 24), por cidade (`lisbon`, `porto`, …) e por termo (modelos).
- **Duplicados:** o mesmo carro pode aparecer em vários sites. A chave de comparação é marca + modelo + ano + km + preço.
- **Referência de mercado grátis:** as páginas de modelo do AutoUncle publicam em CC-BY o preço médio por ano, a faixa de preços e os km medianos.

## Tempo real

- O Standvirtual, o OLX e o CustoJusto têm anúncios com poucos minutos. Consultar de 1 em 1 minuto chega.
- No teste, 24 pedidos em 90 segundos (4 fontes de 15 em 15 s) não deram nenhum bloqueio.
- No Facebook, mesmo com `daysSinceListed=1`, os anúncios aparecem com 8 a 30 minutos de atraso. Para ter a ordem exata seria preciso login (com uma conta secundária).
- Com o GitHub Actions o atraso é de pelo menos 5 minutos, e há o limite de minutos grátis nos repositórios privados. O Facebook provavelmente bloqueia os IPs de datacenter. Para tempo real a sério, o monitor tem de correr no PC ou noutra máquina com IP doméstico.

## Repositórios GitHub a aproveitar (as ideias)

- BoPeng/ai-marketplace-monitor: pontuação dos negócios
- carmolim/olx-monitor: alertas de descida de preço
- XD-MHLOO/facebook-gql-scraper: plano B para o Facebook com cookies

## Atualização (21/11/2026): Facebook

- A pesquisa por texto (`/marketplace/<cidade>/search/?query=carro`) passou a redirecionar para o login, mesmo a partir de casa.
- A categoria de veículos (`/marketplace/<cidade>/vehicles/?minPrice=…&maxPrice=…&sortBy=creation_time_descend&daysSinceListed=1`) continua aberta sem login a partir de casa: são 24 anúncios por pedido, nas 7 cidades. Ignora a pesquisa por texto e traz também motas e acessórios.
- A partir dos servidores do GitHub Actions, a mesma página redireciona para o login. É um bloqueio por IP.
