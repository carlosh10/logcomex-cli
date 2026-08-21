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
lx login --email voce@logcomex.com          # pede o código
lx login --email voce@logcomex.com --code NNNNNN
lx whoami
lx ws use demonstrativa                     # conta com dado; CSS Log é vazia
```

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

`find` grava o recorte em `~/.config/lx/current-scope.json`. `rule add --include "country: CL"` entra como filtro da API.

`does-not-include` e tag inteligente **não existem no backend**. O CLI recusa, de propósito: filtrar a página e somar mentiria o FOB.

## Painéis (macro → micro)

Três layouts reutilizáveis, mesmo recorte:

```bash
lx panel breaks     # universo no tempo → zoom da seleção → ranking
lx panel stacks     # composição mês a mês (empilhado, inclui Outros)
lx panel lines      # 5 séries no tempo (sem Outros)
```

`--break importer,exporter` (padrão). `--out arquivo.png`.

Exemplos (NCM 22042100 · cabernet franc · 12m):

- `examples/breaks-cabernet-franc.png`
- `examples/stacks-cabernet-franc.png`
- `examples/lines-cabernet-franc.png`


## O que o backend ainda precisa (pra agente de verdade)

1. `does-not-include` e regras no **universo**, não na página.
2. Tag / `--by tag` no agregado e na série.
3. OpenAPI com enum de `dimension` (`importer`, `exporter`, `year_month`…). `dimension=month` dá 400.
4. Envelope estável: `{contract, success, scope, coverage, totals, data, nextCursor, warnings}`. Coverage honesto no `/graph`.
5. Auth de serviço (API key / service-session). Cookie + OTP não escala pra agente.
6. Um nome só: hoje `/products/analyses` é agregado; `/company-analyses` é job de chat.

Série = `dimension=year_month`. Grafo de comércio = `/products/graph`. Não precisa de endpoint novo pra isso.

## Comandos crus (alias)

`lx ncm`, `lx company`, `lx shipments`, `lx comexstat`, `lx tracking`, `lx ocr`, `lx analyses` — JSON cru da API.

## Arquivos

| | |
|---|---|
| `lx.py` | CLI |
| `panel.py` / `panel_build.py` | render dos painéis |
| `panels/*.json` | layouts |
| `openapi.json` | spec da platform (v1.1.0) |
