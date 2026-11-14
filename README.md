# Skorpa Data

Cliente SQL desktop (estilo IBExpert) em Python + PySide6 (Qt).

## Rodar

    ./run.sh

Primeira instalação em outra máquina:

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

### macOS

    ./macos/criar_app.sh

Cria a `.venv` (se não existir, usando o Python do Homebrew) e instala
`~/Applications/Skorpa Data.app`, com ícone, que abre pelo Launchpad/Spotlight e pode
ser fixado no Dock. O app roda direto deste repositório, então `git pull` já vale no
próximo clique; rode o script de novo só se mover a pasta ou recriar a `.venv`.
No Mac, os atalhos com Ctrl usam ⌘ (Cmd) e as senhas ficam no Keychain.

## Bancos suportados
Firebird/InterBase, PostgreSQL, MySQL/MariaDB, SQL Server, Oracle e SQLite.

## Onde ficam os dados
- Conexões: `~/.config/sqlath/connections.json` (sem senhas)
- Senhas: chaveiro do sistema (Secret Service/KWallet; Keychain no macOS), serviço `sqlath`
- Histórico de SQL e abas abertas: `~/.config/sqlath/`

## Atalhos
| Ação | Atalho |
|---|---|
| Executar seleção / comando sob o cursor | F9 ou Ctrl+Enter |
| Executar script inteiro | Ctrl+F9 ou Ctrl+Shift+Enter |
| Commit / Rollback | Ctrl+Alt+C / Ctrl+Alt+R |
| Nova aba | Ctrl+T |
| Autocompletar | Ctrl+Espaço |
| Comentar linhas | Ctrl+/ |
| Mostrar/recolher barra lateral de bancos | Ctrl+B |
| Mostrar/recolher painel de resultados | Ctrl+J |

Cada aba tem conexão e transação próprias. Sem auto-commit, nada é gravado
até o Commit; fechar a aba com alterações pendentes pergunta o que fazer.
Conexões de **Produção** pedem confirmação antes de qualquer comando de alteração
e antes do commit; conexões marcadas "somente leitura" bloqueiam alterações.

## Cores
Usa a paleta do Skorpa. Veja [docs/cores.md](docs/cores.md).
