"""Tema claro/escuro e ícones (Tabler Icons, MIT — ver resources/icons/LICENSE-tabler)."""
from __future__ import annotations

import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from . import cores

ICON_DIR = Path(__file__).parent / "resources" / "icons"

# No macOS 1pt = 1px (72 dpi lógico); no Linux/Windows 1pt ≈ 1,33px (96 dpi).
# Sem isso as fontes ficam ~25% menores no Mac.
FONT_SCALE = 4 / 3 if sys.platform == "darwin" else 1.0


def pt(size: float) -> int:
    """Converte um tamanho de fonte pensado para o Linux no equivalente da plataforma atual."""
    return round(size * FONT_SCALE)

def _roles(k: dict) -> dict:
    """Papéis usados pelo app, todos tirados da paleta do Skorpa (ver docs/cores.md)."""
    return dict(
        # editor de SQL
        editor_bg=k["blackColor"], editor_fg=k["whiteColor"], line_bg=k["cardBgHover"],
        gutter_bg=k["cardBg"], gutter_fg=k["greyColor"], gutter_cur=k["whiteColor"],
        kw=k["blueColor"], str=k["positiveColor"], num=k["tealColor"], com=k["mutedColor"],
        param=k["accentColor"], err_bg=k["negativeColorAlpha15"], null=k["greyColor"],
        # textos e ícones
        muted=k["mutedColor"], icon=k["secondaryWhite"],
        green=k["positiveColor"], red=k["negativeColor"], blue=k["blueColor"],
        orange=k["accentColor"], yellow=k["starColor"],
    )


THEMES = {name: _roles(pal) for name, pal in cores.PALETTES.items()}

_current = "dark"  # como no Skorpa, o escuro é o padrão


def name() -> str:
    return _current


def c(key: str) -> str:
    """Cor (papel) do tema atual."""
    return THEMES[_current][key]


def skorpa(var: str) -> str:
    """Variável da paleta do Skorpa no tema atual (ex.: 'blueColor', 'cardBg')."""
    return cores.PALETTES[_current][var]


def qcolor(value: str) -> QColor:
    """QColor de '#rrggbb' ou 'rgba(r,g,b,a)' (formato do globals.css)."""
    return QColor(*cores.rgba_to_qt(value))


def _palette(k: dict) -> QPalette:
    """QPalette montada como as telas do Skorpa: superfície blackColor, cartões cardBg,
    texto whiteColor, secundário mutedColor, destaque blueColor."""
    p = QPalette()
    surface, card, hover = qcolor(k["blackColor"]), qcolor(k["cardBg"]), qcolor(k["cardBgHover"])
    text, muted, blue = qcolor(k["whiteColor"]), qcolor(k["mutedColor"]), qcolor(k["blueColor"])
    p.setColor(QPalette.Window, surface)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, card)
    p.setColor(QPalette.AlternateBase, hover)
    p.setColor(QPalette.ToolTipBase, card)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, muted)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, card)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, qcolor(k["negativeColor"]))
    p.setColor(QPalette.Light, hover)
    p.setColor(QPalette.Midlight, hover)
    p.setColor(QPalette.Mid, qcolor(k["greyColor"]))
    p.setColor(QPalette.Dark, surface)
    p.setColor(QPalette.Shadow, surface)
    p.setColor(QPalette.Link, blue)
    p.setColor(QPalette.Highlight, qcolor(k["blueColorAlpha25"]))
    p.setColor(QPalette.HighlightedText, text)
    if hasattr(QPalette, "Accent"):  # Qt >= 6.6
        p.setColor(QPalette.Accent, blue)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, qcolor(k["greyColor"]))
    return p


def _svg_file(icon_name: str, color: str) -> str:
    """Ícone Tabler já colorido em arquivo temporário (stylesheets do Qt só aceitam url())."""
    out = Path(tempfile.gettempdir()) / f"skorpa-data-{icon_name}-{color.strip('#').lower()}.svg"
    if not out.exists():
        out.write_text((ICON_DIR / f"{icon_name}.svg").read_text(encoding="utf-8").replace("currentColor", color),
                       encoding="utf-8")
    return out.as_posix()


def _stylesheet(k: dict) -> str:
    """O que a QPalette não cobre, seguindo o Skorpa: foco em blueColor, hover em cardBgHover,
    barra de rolagem fina (secondaryGrey sobre cardBg). Abas não definem `color` aqui para não
    apagar a cor de alerta das abas de Produção/Cliente."""
    return f"""
    QLineEdit {{
        background: {k["cardBg"]}; border: 1px solid {k["cardBgHover"]}; border-radius: 6px; padding: 3px 6px;
    }}
    QLineEdit:focus {{ border-color: {k["blueColor"]}; }}
    QTabBar::tab {{
        background: transparent; border: none; border-bottom: 2px solid transparent; padding: 6px 12px;
    }}
    QTabBar::tab:selected {{ border-bottom-color: {k["blueColor"]}; background: {k["blueColorAlpha12"]}; }}
    QTabBar::tab:hover:!selected {{ background: {k["cardBgHover"]}; }}
    QTabBar::close-button {{ image: url("{_svg_file("x", k["mutedColor"])}"); subcontrol-position: right; }}
    QTabBar::close-button:hover {{ image: url("{_svg_file("x", k["negativeColor"])}"); }}
    QTreeView::item:hover, QListView::item:hover {{ background: {k["cardBgHover"]}; }}
    QTreeView::item:selected, QListView::item:selected {{
        background: {k["blueColorAlpha25"]}; color: {k["whiteColor"]};
    }}
    QToolTip {{ background: {k["cardBg"]}; color: {k["whiteColor"]}; border: 1px solid {k["blueColor"]}; }}
    QScrollBar:vertical {{ background: {k["cardBg"]}; width: 10px; margin: 0; }}
    QScrollBar:horizontal {{ background: {k["cardBg"]}; height: 10px; margin: 0; }}
    QScrollBar::handle {{ background: {k["secondaryGrey"]}; border-radius: 3px; margin: 2px; }}
    QScrollBar::handle:vertical {{ min-height: 24px; }}
    QScrollBar::handle:horizontal {{ min-width: 24px; }}
    QScrollBar::handle:hover {{ background: {k["mutedColor"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    """


def apply(theme: str, save=True):
    global _current
    _current = theme if theme in THEMES else "dark"
    app = QApplication.instance()
    try:  # Qt >= 6.8: não deixa o esquema do sistema sobrescrever o tema escolhido
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if _current == "dark" else Qt.ColorScheme.Light)
    except AttributeError:
        pass
    app.setStyle("Fusion")
    app.setPalette(_palette(cores.PALETTES[_current]))
    app.setStyleSheet(_stylesheet(cores.PALETTES[_current]))
    # widgets com stylesheet guardam a paleta antiga: força recalcular
    for w in app.allWidgets():
        ss = w.styleSheet()
        if ss:
            w.setStyleSheet("")
            w.setStyleSheet(ss)
    if save:
        QSettings("sqlath", "sqlath").setValue("theme", _current)


def load_saved():
    apply(QSettings("sqlath", "sqlath").value("theme", "dark"), save=False)


@lru_cache(maxsize=None)
def _icon(icon_name: str, color: str) -> QIcon:
    path = ICON_DIR / f"{icon_name}.svg"
    if not path.exists():
        return QIcon()
    svg = path.read_text(encoding="utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for size in (16, 20, 24, 32, 48):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        ic.addPixmap(pm)
    return ic


def icon(icon_name: str, color_key: str = "icon") -> QIcon:
    """Ícone Tabler na cor `color_key` do tema (ou cor literal '#rrggbb')."""
    return _icon(icon_name, color_key if color_key.startswith("#") else c(color_key))


# Logos dos bancos em resources/icons/db/<driver>.svg (ver LEIAME.md lá).
# Cor usada nos logos monocromáticos (os coloridos ignoram) e no ícone genérico de fallback.
DB_ICON_COLORS = {
    "firebird": "#f4511e",
    "postgres": "#336791",
    "mysql": "#4479a1",
    "mssql": "#cc2927",
    "oracle": "#f80000",
    "sqlite": "#0f80cc",
}


def db_icon(driver_key: str) -> QIcon:
    """Logo do tipo de banco (PostgreSQL = elefante, Firebird = pássaro de fogo…)."""
    color = DB_ICON_COLORS.get(driver_key, c("blue"))
    if (ICON_DIR / "db" / f"{driver_key}.svg").exists():
        return _icon(f"db/{driver_key}", color)
    return _icon("database", color)

