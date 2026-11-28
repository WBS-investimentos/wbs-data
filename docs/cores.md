# Cores do WBS Data

O WBS Data é um projeto Skorpa e usa **a mesma paleta do Skorpa** (`src/app/globals.css`
do repositório Skorpa), com os mesmos nomes de variável.

- Fonte única das cores: [`sqlath/cores.py`](../sqlath/cores.py) (`DARK` = `:root`, `LIGHT` = `:root[data-theme="light"]`).
- Quem aplica as cores no Qt: [`sqlath/theme.py`](../sqlath/theme.py) (QPalette + uma folha de estilo pequena).

## Regras

1. **Só as variáveis da paleta.** Nenhuma cor fixa (`#rrggbb`, `white`, `Qt.red`…) fora de `cores.py`.
2. **Nenhuma variável nova** sem combinar antes. Se o Skorpa ganhar uma cor nova no `globals.css`, ela entra aqui com o mesmo nome e o mesmo valor.
3. **O claro é o padrão do app** (no Skorpa é o escuro). No tema claro os nomes continuam descrevendo o *papel* herdado do escuro: `whiteColor` é a cor do texto e `blackColor` a da superfície, com os valores trocados.
4. **Texto sobre fundo colorido** (faixas de ambiente, etiqueta de transação pendente) usa `cores.text_on(fundo)`, que escolhe entre o `blackColor` e o `whiteColor` do tema escuro conforme o contraste.

**Exceção:** os logotipos dos bancos (`theme.DB_ICON_COLORS` e `resources/icons/db/`) mantêm as cores das próprias marcas (elefante azul do PostgreSQL etc.).

## Paleta

| Variável | Escuro | Claro (padrão) |
|---|---|---|
| `whiteColor` | `#FAF9F6` | `#1d1d1d` |
| `secondaryWhite` | `#EDEDED` | `#2f2f2f` |
| `blackColor` | `#141414` | `#ffffff` |
| `cardBg` | `#2a2a2a` | `#FAF9F6` |
| `cardBgHover` | `#333333` | `#efeee9` |
| `greyColor` | `grey` (`#808080`) | `#6b6b6b` |
| `secondaryGrey` | `#d3d3d3` | `#6a6a6a` |
| `mutedColor` | `#aaa` | `#757575` |
| `blueColor` | `rgba(114,151,197,1)` (`#7297C5`) | igual |
| `blueColorAlpha12` / `15` / `25` | `blueColor` com 12% / 15% / 25% | igual |
| `positiveColor` | `#22B37F` | igual |
| `negativeColor` | `#f7525f` | igual |
| `positiveInputColor` | `#4caf50` | igual |
| `negativeInputColor` | `#f44336` | igual |
| `negativeColorAlpha15` | `rgba(244,67,54,0.15)` | igual |
| `starColor` | `#f5c518` | igual |
| `accentColor` | `#FF6A00` | igual |
| `tealColor` | `#26C6DA` | igual |

## Como cada cor é usada (mesmos papéis das telas do Skorpa)

### Interface

| Papel | Variável |
|---|---|
| Fundo da janela, barras de ferramentas, títulos dos painéis | `blackColor` |
| Áreas de conteúdo: editor, árvore de bancos, tabelas, campos, botões, dicas | `cardBg` |
| Hover (itens da árvore e abas), linha atual do editor, linhas alternadas | `cardBgHover` |
| Texto principal | `whiteColor` |
| Texto secundário, placeholders | `mutedColor` |
| Texto desabilitado, número das linhas, `NULL` nos resultados | `greyColor` |
| Ícones da interface | `secondaryWhite` |
| Destaque/foco: borda do campo focado, aba selecionada, links | `blueColor` |
| Fundo da aba selecionada | `blueColorAlpha12` |
| Seleção (texto, itens, células) | `blueColorAlpha25` |
| Barra de rolagem: trilho / alça / alça com hover | `cardBg` / `secondaryGrey` / `mutedColor` |
| Sucesso (teste de conexão ok, commit) | `positiveColor` |
| Erro (mensagens, sublinhado do erro no SQL) | `negativeColor`, fundo `negativeColorAlpha15` |
| Atenção (transação pendente, alterações não salvas) | `accentColor` |

### Ícones das ações (`theme.icon(nome, papel)`)

| Papel | Variável |
|---|---|
| `icon` | `secondaryWhite` |
| `green` | `positiveColor` |
| `red` | `negativeColor` |
| `blue` | `blueColor` |
| `orange` | `accentColor` |
| `yellow` | `starColor` |
| `muted` | `mutedColor` |

### Realce do SQL

| Elemento | Variável |
|---|---|
| Palavras-chave (`SELECT`, `FROM`…) | `blueColor` (negrito) |
| Textos `'…'` | `positiveColor` |
| Números | `tealColor` |
| Comentários | `mutedColor` (itálico) |
| Parâmetros `:nome` | `accentColor` |

### Ambientes das conexões (`storage.ENV_COLORS`)

Iguais nos dois temas. Aparecem na bolinha da aba, no grupo da barra lateral e na faixa do topo.

| Ambiente | Variável |
|---|---|
| Cliente | `negativeInputColor` (faixa sólida) |
| Produção | `negativeColor` |
| Homologação | `accentColor` |
| Desenvolvimento | `blueColor` |
| Teste | `tealColor` |
| Local | `positiveColor` |

## No código

```python
from sqlath import theme, cores

theme.c("red")              # papel do tema atual (tabelas acima)
theme.skorpa("cardBg")      # variável do Skorpa no tema atual
theme.qcolor(theme.skorpa("blueColorAlpha25"))  # QColor aceita também o formato rgba() do globals.css
cores.text_on(cor_de_fundo) # texto legível sobre um fundo colorido
```
