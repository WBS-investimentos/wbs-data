"""Tema claro/escuro e ícones (Tabler Icons, MIT — ver resources/icons/LICENSE-tabler)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ICON_DIR = Path(__file__).parent / "resources" / "icons"

THEMES = {
    "light": dict(
        editor_bg="#ffffff", editor_fg="#1f1f1f", line_bg="#f5f8ff", gutter_bg="#f3f3f3",
        gutter_fg="#9a9a9a", gutter_cur="#202020", kw="#0033b3", str="#067d17", num="#1750eb",
        com="#8c8c8c", param="#871094", err_bg="#fdecea", null="#a0a0a0", muted="#555555",
        icon="#455a64", green="#2e7d32", red="#c62828", blue="#1565c0", orange="#ef6c00", yellow="#f9a825",
    ),
    "dark": dict(
        editor_bg="#1e1f22", editor_fg="#bcbec4", line_bg="#26282e", gutter_bg="#1e1f22",
        gutter_fg="#5a5d63", gutter_cur="#a1a3ab", kw="#cf8e6d", str="#6aab73", num="#2aacb8",
        com="#7a7e85", param="#c77dbb", err_bg="#4a2526", null="#6f737a", muted="#9aa0a6",
        icon="#c0c4cc", green="#66bb6a", red="#ef5350", blue="#64b5f6", orange="#ffa726", yellow="#ffd54f",
    ),
}

_current = "light"


def name() -> str:
    return _current


def c(key: str) -> str:
    """Cor do tema atual."""
    return THEMES[_current][key]


def _dark_palette() -> QPalette:
    p = QPalette()
    window, base, alt, text = QColor("#2b2d30"), QColor("#1e1f22"), QColor("#26282c"), QColor("#dfe1e5")
    disabled = QColor("#6f737a")
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.ToolTipBase, QColor("#3c3f41"))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, disabled)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#3c3f41"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, Qt.red)
    p.setColor(QPalette.Light, QColor("#4a4d50"))
    p.setColor(QPalette.Midlight, QColor("#3c3f41"))
    p.setColor(QPalette.Mid, QColor("#323437"))
    p.setColor(QPalette.Dark, QColor("#1b1c1e"))
    p.setColor(QPalette.Shadow, QColor("#000000"))
    p.setColor(QPalette.Link, QColor("#64b5f6"))
    p.setColor(QPalette.Highlight, QColor("#2f65ca"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def _light_palette() -> QPalette:
    # paleta explícita: o Fusion do Qt 6 segue o modo escuro do sistema por padrão
    p = QPalette()
    window, base, text = QColor("#f3f3f3"), QColor("#ffffff"), QColor("#1f1f1f")
    disabled = QColor("#a0a0a0")
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor("#f6f8fa"))
    p.setColor(QPalette.ToolTipBase, QColor("#ffffdc"))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor("#8a8a8a"))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#ececec"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, Qt.red)
    p.setColor(QPalette.Light, QColor("#ffffff"))
    p.setColor(QPalette.Midlight, QColor("#e3e3e3"))
    p.setColor(QPalette.Mid, QColor("#b8b8b8"))
    p.setColor(QPalette.Dark, QColor("#9f9f9f"))
    p.setColor(QPalette.Shadow, QColor("#767676"))
    p.setColor(QPalette.Link, QColor("#1565c0"))
    p.setColor(QPalette.Highlight, QColor("#308cc6"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def apply(theme: str, save=True):
    global _current
    _current = theme if theme in THEMES else "light"
    app = QApplication.instance()
    try:  # Qt >= 6.8: não deixa o esquema do sistema sobrescrever o tema escolhido
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if _current == "dark" else Qt.ColorScheme.Light)
    except AttributeError:
        pass
    app.setStyle("Fusion")
    app.setPalette(_dark_palette() if _current == "dark" else _light_palette())
    # widgets com stylesheet guardam a paleta antiga: força recalcular
    for w in app.allWidgets():
        ss = w.styleSheet()
        if ss:
            w.setStyleSheet("")
            w.setStyleSheet(ss)
    if save:
        QSettings("sqlath", "sqlath").setValue("theme", _current)


def load_saved():
    apply(QSettings("sqlath", "sqlath").value("theme", "light"), save=False)


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
