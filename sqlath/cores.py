"""Paleta do Skorpa — a mesma do `src/app/globals.css` do projeto Skorpa.

Regra (igual ao Skorpa): toda cor do app sai daqui. Nada de cor hardcoded em outros
arquivos e nada de variável nova sem combinar antes. Detalhes e papéis de cada cor em
docs/cores.md.

Como no Skorpa, o tema escuro é o padrão e os nomes descrevem o PAPEL herdado dele,
não o valor literal: no tema claro `whiteColor` é a cor do texto e `blackColor` a da
superfície, com os valores trocados.
"""
from __future__ import annotations

# :root (tema escuro, padrão)
DARK = {
    "whiteColor": "#FAF9F6",
    "secondaryWhite": "#EDEDED",
    "blueColor": "#7297C5",           # rgba(114,151,197,1)
    "blueColorAlpha12": "rgba(114,151,197,0.12)",
    "blueColorAlpha15": "rgba(114,151,197,0.15)",
    "blueColorAlpha25": "rgba(114,151,197,0.25)",
    "blackColor": "#141414",
    "greyColor": "#808080",           # grey
    "secondaryGrey": "#d3d3d3",
    "mutedColor": "#aaaaaa",
    "positiveColor": "#22B37F",
    "negativeColor": "#f7525f",
    "positiveInputColor": "#4caf50",
    "negativeInputColor": "#f44336",
    "negativeColorAlpha15": "rgba(244,67,54,0.15)",
    "cardBg": "#2a2a2a",
    "cardBgHover": "#333333",
    "starColor": "#f5c518",
    "accentColor": "#FF6A00",
    "tealColor": "#26C6DA",
}

# :root[data-theme="light"] — só estes valores mudam
LIGHT = {
    **DARK,
    "whiteColor": "#1d1d1d",
    "secondaryWhite": "#2f2f2f",
    "blackColor": "#ffffff",
    "greyColor": "#6b6b6b",
    "secondaryGrey": "#6a6a6a",
    "mutedColor": "#757575",
    "cardBg": "#FAF9F6",
    "cardBgHover": "#efeee9",
}

PALETTES = {"dark": DARK, "light": LIGHT}


def rgba_to_qt(value: str):
    """'rgba(r,g,b,a)' ou '#rrggbb' → (r, g, b, a 0-255), para montar QColor."""
    v = value.strip()
    if v.startswith("rgba("):
        r, g, b, a = (x.strip() for x in v[5:-1].split(","))
        return int(r), int(g), int(b), round(float(a) * 255)
    v = v.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255


def text_on(bg: str) -> str:
    """Texto legível sobre um fundo colorido (faixas de ambiente, etiquetas): escuro ou claro do Skorpa."""
    r, g, b, _ = rgba_to_qt(bg)
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return DARK["blackColor"] if lum > 0.55 else DARK["whiteColor"]
