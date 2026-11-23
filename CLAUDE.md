# WBS Data

Cliente SQL desktop (Python + PySide6), parte do ecossistema WBS (antigo Skorpa; o repositório
e os nomes da paleta continuam "Skorpa"). Roda no Linux e no macOS.

## Cores

Usa a paleta do Skorpa (mesmos nomes e valores do `globals.css` do projeto Skorpa).
Tudo está em [`docs/cores.md`](docs/cores.md).

**Regra absoluta de cores:** usar apenas as variáveis de `sqlath/cores.py`, via `theme.c(papel)`
ou `theme.skorpa(variável)`. Nunca usar cor hardcoded fora de `cores.py`. Nunca criar variável
de cor nova sem permissão explícita do usuário.

**Exceção:** os logos dos bancos mantêm as cores das marcas. Fora isso, cor fora da paleta só
quando o usuário pedir explicitamente, e desde que seja bem diferente das cores já disponíveis.

## Compatibilidade

- Tudo que é visível precisa funcionar no Linux e no macOS. No Mac, `Ctrl` nos atalhos vira `⌘`
  sozinho (Qt). Tamanhos de fonte passam por `theme.pt()`.
- O nome visível é "WBS Data" (barra do topo: "WBS" em Libre Caslon Display, como no Skorpa).
  Os nomes internos (`sqlath/`, `~/.config/sqlath`, serviço `sqlath` no chaveiro,
  `QSettings("sqlath", "sqlath")`, `theme.skorpa`, chave `skorpa_data_connection` dos arquivos
  exportados) ficam como estão para não perder dados já salvos.
