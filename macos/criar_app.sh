#!/usr/bin/env bash
# Cria "Skorpa Data.app" (com ícone) para abrir o Skorpa Data pelo Launchpad/Dock/Spotlight no macOS.
#
#   ./macos/criar_app.sh                 # instala em ~/Applications
#   ./macos/criar_app.sh /Applications   # ou em outra pasta
#
# O .app não copia o código: ele roda direto deste repositório (o mesmo usado no Linux),
# então um `git pull` já vale no próximo clique. Rode o script de novo se mover o
# repositório ou recriar a .venv com outra versão do Python.
set -euo pipefail

[[ "$(uname)" == "Darwin" ]] || { echo "Este script é só para macOS (no Linux use ./run.sh)."; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Applications}"
APP="$DEST/Skorpa Data.app"
ICON_PNG="$REPO/sqlath/resources/app_icon_macos.png"

# 1) Ambiente Python (.venv) — prefere o Python do Homebrew ao do sistema (3.9, antigo)
if [[ ! -x "$REPO/.venv/bin/python" ]]; then
    PY=""
    for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
        if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    done
    [[ -n "$PY" ]] || { echo "Python 3 não encontrado. Instale com: brew install python"; exit 1; }
    echo "Criando .venv com $("$PY" --version)…"
    "$PY" -m venv "$REPO/.venv"
    "$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"
fi

# Binário real do Python (dentro do Python.app do framework). Ele é ligado dentro do nosso
# .app para que o menu e o Dock mostrem "Skorpa Data" em vez de "Python".
read -r PYBIN SITE < <("$REPO/.venv/bin/python" - <<'EOF'
import sys, sysconfig
from pathlib import Path
base = Path(sys.base_prefix)
app = base / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
print(app if app.exists() else Path(sys._base_executable).resolve(), sysconfig.get_paths()["purelib"])
EOF
)

# 2) Monta o bundle (e remove o app com o nome antigo, "SQL Ath")
rm -rf "$APP" "$DEST/SQL Ath.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
ln -s "$PYBIN" "$APP/Contents/MacOS/Skorpa Data"

cat > "$APP/Contents/MacOS/sqlath" <<EOF
#!/bin/bash
# Gerado por macos/criar_app.sh — repositório: $REPO
REPO="$REPO"
if [[ ! -d "\$REPO/sqlath" ]]; then
    osascript -e 'display alert "Skorpa Data" message "Repositório não encontrado em:\n$REPO\n\nRode macos/criar_app.sh de novo."'
    exit 1
fi
cd "\$REPO"
export PYTHONPATH="\$REPO:$SITE"
export PYTHONNOUSERSITE=1
exec "\$(dirname "\$0")/Skorpa Data" -m sqlath "\$@"
EOF
chmod +x "$APP/Contents/MacOS/sqlath"

# 3) Ícone .icns a partir do PNG do app
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ICONSET="$TMP/sqlath.iconset"
mkdir "$ICONSET"
for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON_PNG" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$ICON_PNG" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/sqlath.icns"

VERSION="$(sed -n 's/^__version__ *= *["'\'']\(.*\)["'\'']/\1/p' "$REPO/sqlath/__init__.py")"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Skorpa Data</string>
    <key>CFBundleDisplayName</key><string>Skorpa Data</string>
    <key>CFBundleIdentifier</key><string>br.com.skorpadata</string>
    <key>CFBundleExecutable</key><string>sqlath</string>
    <key>CFBundleIconFile</key><string>sqlath</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${VERSION:-1.0}</string>
    <key>CFBundleVersion</key><string>${VERSION:-1.0}</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
EOF

# Avisa o Finder/Launchpad do app novo (e do ícone)
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true

echo "Pronto: $APP"
echo "Abra pelo Launchpad/Spotlight (\"Skorpa Data\") e, se quiser, fixe no Dock."
