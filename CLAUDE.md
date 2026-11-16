# Skorpa Data

Cliente SQL desktop (Python + PySide6), parte do ecossistema Skorpa. Roda no Linux e no macOS.

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
- O nome visível é "Skorpa Data". Os nomes internos (`sqlath/`, `~/.config/sqlath`, serviço
  `sqlath` no chaveiro, `QSettings("sqlath", "sqlath")`) ficam como estão para não perder dados
  já salvos.
