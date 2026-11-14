"""Animações suaves para recolher/mostrar a barra lateral (dock) e o painel de resultados (splitter)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, Qt, QVariantAnimation
from PySide6.QtWidgets import QDockWidget, QGraphicsOpacityEffect, QMainWindow, QSplitter, QWidget

DURATION = 220  # ms
QWIDGETSIZE_MAX = 16777215


def _run(owner: QObject, start: float, end: float, on_step, on_done, easing=QEasingCurve.OutCubic):
    """Interpola de start a end chamando on_step(valor); a animação anterior do mesmo dono é cancelada."""
    old = getattr(owner, "_sqlath_anim", None)
    if old is not None:
        old.stop()
    a = QVariantAnimation(owner)
    a.setDuration(DURATION)
    a.setEasingCurve(easing)
    a.setStartValue(float(start))
    a.setEndValue(float(end))
    a.valueChanged.connect(lambda v: on_step(float(v)))
    a.finished.connect(on_done)
    a.finished.connect(lambda: setattr(owner, "_sqlath_anim", None))
    owner._sqlath_anim = a
    a.start(QVariantAnimation.DeleteWhenStopped)


def _fade(widget: QWidget) -> QGraphicsOpacityEffect:
    eff = widget.graphicsEffect()
    if not isinstance(eff, QGraphicsOpacityEffect):
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
    return eff


def _clear_fade(widget: QWidget):
    # sem o efeito o widget volta a ser pintado direto (mais leve e sem perder nitidez)
    widget.setGraphicsEffect(None)


def is_animating(owner: QObject) -> bool:
    return getattr(owner, "_sqlath_anim", None) is not None


# ----------------------------------------------------------------- dock
def toggle_dock(win: QMainWindow, dock: QDockWidget, show: bool):
    """Desliza a largura do dock (com fade do conteúdo) até sumir ou até a largura anterior."""
    if dock.isFloating() or show == dock.isVisible() and not is_animating(dock):
        dock.setVisible(show)
        return
    content = dock.widget()
    if not show and not is_animating(dock):
        dock._sqlath_width = dock.width()
    target = max(getattr(dock, "_sqlath_width", 0) or 0, 180)
    start = dock.width() if dock.isVisible() else 0
    end = target if show else 0
    eff = _fade(content)

    def step(v):
        dock.setFixedWidth(int(v))
        eff.setOpacity(max(0.0, min(1.0, v / target)))

    def done():
        dock.setMinimumWidth(0)
        dock.setMaximumWidth(QWIDGETSIZE_MAX)
        _clear_fade(content)
        if show:
            win.resizeDocks([dock], [target], Qt.Horizontal)
        else:
            dock.hide()

    if show:
        dock.setFixedWidth(0)
        eff.setOpacity(0.0)
        dock.show()
    _run(dock, start, end, step, done, QEasingCurve.OutCubic if show else QEasingCurve.InOutCubic)


# ------------------------------------------------------------- splitter
def toggle_splitter_pane(split: QSplitter, pane: QWidget, show: bool, animate: bool = True):
    """Recolhe/mostra `pane` (último filho do splitter) animando o tamanho dele."""
    idx = split.indexOf(pane)
    if not animate or not split.isVisible():
        if not show and pane.isVisible():
            pane._sqlath_size = split.sizes()[idx]
        pane.setVisible(show)
        return
    if show == pane.isVisible() and not is_animating(pane):
        return
    sizes = split.sizes()
    total = sum(sizes)
    if not show and not is_animating(pane):
        pane._sqlath_size = sizes[idx]
    target = min(max(getattr(pane, "_sqlath_size", 0) or 0, 120), int(total * 0.8) or 120)
    start = sizes[idx] if pane.isVisible() else 0
    end = target if show else 0
    pane.setMinimumHeight(1)  # sem isso o splitter "pula" ao passar do tamanho mínimo do painel
    eff = _fade(pane)

    def step(v):
        cur = split.sizes()
        tot = sum(cur)
        cur[idx] = int(v)
        others = [i for i in range(len(cur)) if i != idx]
        rest = tot - int(v)
        for i in others:
            cur[i] = rest // len(others)
        split.setSizes(cur)
        eff.setOpacity(max(0.0, min(1.0, v / target)))

    def done():
        pane.setMinimumHeight(0)
        _clear_fade(pane)
        if not show:
            pane.hide()

    if show:
        cur = split.sizes()
        pane.show()
        cur[idx] = 0
        split.setSizes(cur)
        eff.setOpacity(0.0)
    _run(pane, start, end, step, done, QEasingCurve.OutCubic if show else QEasingCurve.InOutCubic)
