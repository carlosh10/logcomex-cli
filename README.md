# lx — CLI de intel da platform.logcomex.ai

CLI para agente (e para humano no terminal). JSON no stdout. Cobre o miolo de intel: **produto, empresa, embarque**.

Não substitui a UI. É um jeito estável de consultar o mesmo dado, sem somar página recortada.

## Instalação

```bash
git clone <este-repo> logcomex-cli
cd logcomex-cli
python3 -m venv .venv
.venv/bin/pip install matplotlib   # só para lx panel
chmod +x lx.py
mkdir -p ~/.local/bin
ln -sf "$(pwd)/lx.py" ~/.local/bin/lx
```

`lx` usa a sessão em `~/.config/lx/` (cookie + OTP). **Não commitar, não copiar de outra máquina.**

## Login

```bash
lx login --email voce@logcomex.com          # pede o código no prompt (não na argv)
lx login --email voce@logcomex.com --code-file /path/para/otp
lx whoami
lx ws use demonstrativa                     # conta com dado; CSS Log é vazia
```

OTP: prompt interativo ou `--code-file`, nunca na argv (não entra no history / `ps`).
Senha: só via `--password-file`, nunca na argv.

## Uso (intel)

O caminho é sempre o mesmo: achar um recorte, olhar, abrir ficha, opcionalmente um painel.

```bash
lx find product --ncm 22042100 --period 12m
lx find product --ncm 22042100 --period 12m --text "cabernet franc"
lx view series --by month
lx view agg --by importer --limit 5
lx view agg --by exporter --limit 5
lx view graph --limit 15
lx profile company <entity_id>
```

`find` grava o recorte em `~/.config/lx/current-scope.json`. `rule add --include "country: CL"` entra como filtro da API (produto). `does-not-include` e tag inteligente **não existem no backend**. O CLI recusa, de propósito: filtrar a página e somar mentiria o FOB.

## Empresa (company intelligence, não o catálogo)

`find company` agora é **company_rds / logcomex_company_intelligence** — o universo honesto (`totais.linhas`, `exato=true`). Não é GET `/api/market-intelligence/companies` (catálogo fino ~6k cards; `profile=Importador` ignorado; FOB do card sem período).

O catálogo fino continua explícito: `lx company search` / `lx company get`.

```bash
lx find company --pais BRASIL --categoria importadores --period 12m --text "vinho"
lx rule include --field fob_12m --op gt --value 1000000
lx view rows          # página + totais.linhas no envelope (precisa do job ou fixture)
lx view count         # só totais.linhas / exato / query_id — nunca soma a página
lx profile company 12345678000190
lx watch              # count; recusa series/agg (não há histograma nativo)
```

`--period 12m` → `ultima_operacao` + `desde` (hoje−12m). Outros períodos **não** são mapeados. `--text` → `descricao_produto` (sem `query`/`description`). `rule include` vira `filtros_avancados` e/ou `fob_12m_min`/`max`. `view agg|series|graph` para empresa continua recusado: `faixa_fob` é rótulo do card, não agregado.

**Invoke:** o OpenAPI ao vivo **não** tem GET de company-intel com esses filtros. A UI manda um chat-run (`POST /api/conversations` + `PUT …/tools` + `POST …/chat-runs`) com `[[LOGCOMEX_TOOL_CONTEXT_V1]]`. `find` **não** abre conversa.

```bash
lx intel query                 # escape hatch: job de chat (precisa --connection-id / --model)
lx intel wait                  # poll até terminal
lx intel result                # lê totais.linhas dos events e grava no scope
```

Sem job, `view rows|count` falha com `company_intel_job_required` (ou usa `LX_COMPANY_INTEL_FIXTURE` nos testes). Não some FOB da página: linha é CNPJ-estabelecimento; `fob_12m` é grupo_economico e `somavel_entre_estabelecimentos=false`.

Produto (`find product` / `/products/analyses`) e painéis de NCM **não mudam**.

## Painéis (macro → micro)

Três layouts reutilizáveis, mesmo recorte:

```bash
lx panel breaks     # universo no tempo → zoom da seleção → 2×2 quebras
lx panel stacks     # composição mês a mês (empilhado, inclui Outros)
lx panel lines      # 5 séries no tempo (sem Outros)
lx panel dims       # dimensões de quebra de produto + ranking p/ brief
```

`--break importer,exporter,market,state` (padrão, 2 séries + 2×2 barras). Clássico 2 quebras: `--break importer,exporter`. Só 2 ou 4 dims; `origin`/`country` → `market`. `--out arquivo.png` (padrão: diretório atual).

`lx view agg --by` aceita só o enum de `/products/analyses`. Ranking e aliases: `lx panel dims`.

### Quebras nativas vs refino

**Quebras** (`lx view agg --by` / `lx panel --break`) — só o enum. Ranking e aliases: `lx panel dims`.

**Refino** (não é barra de painel) — estreita o recorte. `brand` e `model` aparecem no card (`brand`, `model`, `keywords`, `description`, `attributesMain`) e como filtro `attribute` `{name,value}`. Live: `name=brand` (ex. THUNDERX3). `dimension=brand` e `dimension=model` dão 400 — não inventar histograma de marca como quebra nativa. Pedido futuro ao Helmuth: `dimension=brand|model`.

`--text` no `find` vira `query`. Não há `--attribute` / `--keywords` no find; use `rule add --include`:

```bash
lx find product --ncm 94013900 --period 12m --text "gamer"
lx rule add brand --include 'attr: {"name":"brand","value":"THUNDERX3"}'
lx rule add brand --include "brand: THUNDERX3"          # mesmo filtro
lx rule add kw --include "keywords: gamer chair"
```

`attributesMain` comuns em gamer / air fryer / vinho: `brand`, `color`, `year`, `packed`, `electric_current`, `composition`, `destination`, `dimensions`.

Exemplos (NCM 22042100 · cabernet franc · 12m):

- `examples/breaks-cabernet-franc.png`
- `examples/stacks-cabernet-franc.png`
- `examples/lines-cabernet-franc.png`

## Looks e dashboards (local)

**Sala** (explore) = filtros do recorte que valem em todo tile: entity, ncm, period, região, regras que não são texto.

**Quadro** (look) = zoom extra em alguns tiles: query/text/description/keywords/attribute, mais o jeito de olhar (`--layout` painel ou `--view`).

Dashboard = vários looks + explore compartilhado.

Isso é **só desta máquina**: `~/.config/lx/dashboards/` e `~/.config/lx/looks/` (0600 / dirs 0700). Não é workspace, não é tenant, não é o usuário Logcomex. Helmuth **não** vê. Não há sync.

Looks built-in (sem arquivo): `breaks`, `stacks`, `lines`.

```bash
lx find product --ncm 85166000 --period 12m --text "air fryer"
lx dashboard save airfryer --looks breaks,stacks,lines
lx dashboard show airfryer
lx dashboard show airfryer --period 3m
lx dashboard ls
lx dashboard rm NAME
lx look save tops --view agg --by importer
lx look ls
lx look show tops
lx look rm NAME
```

`dashboard show` reconstrói cada look. `--period` / `--ncm` mudam a sala (todos os tiles). `--text` muda a query do quadro. PNG em `--out` (padrão: diretório atual) como `{dashboard}-{look}.png`.

Não use `explore`/`look` como sinônimo de `find`/`view`. Os verbos continuam find / view / panel.

## O que o backend ainda precisa (pra agente de verdade)

1. `does-not-include` e regras no **universo**, não na página.
2. Tag / `--by tag` no agregado e na série.
3. OpenAPI com enum de `dimension` (`importer`, `exporter`, `market`, `year_month`…). `dimension=month` e `dimension=origin_country` dão 400 no produto — origem prática é `market`. `dimension=brand|model` também 400.
4. Envelope estável: `{contract, success, scope, coverage, totals, data, nextCursor, warnings}`. Coverage honesto no `/graph`.
5. Auth de serviço (API key / service-session). Cookie + OTP não escala pra agente.
6. Um nome só: hoje `/products/analyses` é agregado; `/company-analyses` é job de chat. Company intel ainda não tem GET determinístico — só a ferramenta `logcomex_company_intelligence` via chat-run.
7. Storage de looks/dashboards no tenant. Hoje o CLI grava só em `~/.config/lx/` nesta máquina.

Série = `dimension=year_month`. Grafo de comércio = `/products/graph`. Não precisa de endpoint novo pra isso.

## Comandos crus (alias)

`lx ncm`, `lx company` (catálogo fino), `lx shipments`, `lx comexstat`, `lx tracking`, `lx ocr`, `lx analyses` — JSON cru da API.

## Arquivos

| | |
|---|---|
| `lx.py` | CLI |
| `company_intel.py` | mapeamento company_rds / envelope / parse de rules |
| `catalog.py` | looks e dashboards locais (`~/.config/lx/`) |
| `panel.py` / `panel_build.py` | render dos painéis |
| `panels/*.json` | layouts |
| `fixtures/company_intel_page.json` | página seca para testes (sem credencial) |
| `openapi.json` | spec da platform (v1.1.0) |
