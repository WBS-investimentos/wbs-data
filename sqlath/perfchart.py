"""Gráfico de performance: tempo de cada execução da aba, ao lado do resultado.

Barras empilhadas: execução no servidor (embaixo) + busca da primeira página
(em cima). Desenhado com QPainter, sem dependências extras.
"""
from __future__ import annotations

import datetime
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget

from . import theme

MAX_POINTS = 40


def _fmt(sec: float) -> str:
    ms = sec * 1000
    if ms < 1:
        return f"{ms:.2f} ms"
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{sec:.2f} s"


def _nice_ceiling(v: float) -> float:
    """Arredonda o topo do eixo para 1, 2 ou 5 × 10^n."""
    if v <= 0:
        return 0.001
    exp = math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        top = m * 10 ** exp
        if top >= v:
            return top
    return 10 ** (exp + 1)


class _Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points: list[dict] = []
        self.hover = -1
        self.setMouseTracking(True)
        self.setMinimumSize(220, 140)

    # geometria -------------------------------------------------------
    def _plot_rect(self) -> QRectF:
        fm = QFontMetrics(self.font())
        left = fm.horizontalAdvance("0000 ms") + 10
        return QRectF(left, 8, max(10, self.width() - left - 8), max(10, self.height() - 8 - fm.height() - 8))

    def _bar_rect(self, i: int, plot: QRectF, top: float, total: float) -> QRectF:
        n = max(len(self.points), 8)
        slot = plot.width() / n
        w = max(3.0, slot * 0.7)
        x = plot.left() + i * slot + (slot - w) / 2
        h = plot.height() * (total / top) if top else 0
        return QRectF(x, plot.bottom() - h, w, h)

    def _index_at(self, x: float) -> int:
        plot = self._plot_rect()
        n = max(len(self.points), 8)
        i = int((x - plot.left()) // (plot.width() / n))
        return i if 0 <= i < len(self.points) else -1

    # desenho ---------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().color(self.foregroundRole())
        muted = QColor(theme.c("muted"))
        grid = QColor(muted)
        grid.setAlpha(50)
        plot = self._plot_rect()
        fm = QFontMetrics(self.font())

        if not self.points:
            p.setPen(muted)
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap,
                       "Execute uma consulta para ver\no tempo de cada execução aqui.")
            return

        top = _nice_ceiling(max(pt["exec"] + pt["fetch"] for pt in self.points))
        # grade + rótulos do eixo
        for k in range(5):
            v = top * k / 4
            y = plot.bottom() - plot.height() * k / 4
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(muted)
            p.drawText(QRectF(0, y - fm.height() / 2, plot.left() - 6, fm.height()),
                       Qt.AlignRight | Qt.AlignVCenter, _fmt(v) if k else "0")

        c_exec, c_fetch = QColor(theme.c("blue")), QColor(theme.c("orange"))
        last = len(self.points) - 1
        for i, pt in enumerate(self.points):
            r = self._bar_rect(i, plot, top, pt["exec"] + pt["fetch"])
            h_exec = r.height() * (pt["exec"] / (pt["exec"] + pt["fetch"])) if (pt["exec"] + pt["fetch"]) else 0
            alpha = 255 if i in (last, self.hover) else 150
            ce, cf = QColor(c_exec), QColor(c_fetch)
            ce.setAlpha(alpha)
            cf.setAlpha(alpha)
            p.setPen(Qt.NoPen)
            if not pt["ok"]:
                p.setBrush(QColor(theme.c("red")))
                p.drawRect(r)
                continue
            p.setBrush(cf)
            p.drawRect(QRectF(r.left(), r.top(), r.width(), r.height() - h_exec))
            p.setBrush(ce)
            p.drawRect(QRectF(r.left(), r.bottom() - h_exec, r.width(), h_exec))

        # rótulo do último tempo + legenda
        p.setPen(fg)
        lpt = self.points[last]
        y_leg = plot.bottom() + 4
        p.drawText(QRectF(plot.left(), y_leg, plot.width(), fm.height() + 4), Qt.AlignRight | Qt.AlignVCenter,
                   f"última: {_fmt(lpt['exec'] + lpt['fetch'])}")
        x = plot.left()
        for color, label in ((c_exec, "execução"), (c_fetch, "busca")):
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRect(QRectF(x, y_leg + fm.height() / 2 - 3, 10, 8))
            p.setPen(muted)
            p.drawText(QPointF(x + 14, y_leg + fm.ascent() + 2), label)
            x += 14 + fm.horizontalAdvance(label) + 12

    # interação -------------------------------------------------------
    def mouseMoveEvent(self, ev):
        i = self._index_at(ev.position().x())
        if i != self.hover:
            self.hover = i
            self.update()
        if i < 0:
            QToolTip.hideText()
            return
        pt = self.points[i]
        sql = " ".join(pt["sql"].split())
        sql = sql if len(sql) <= 120 else sql[:117] + "…"
        tip = (f"<b>#{pt['n']}</b> · {pt['ts']}<br>"
               f"Total: <b>{_fmt(pt['exec'] + pt['fetch'])}</b><br>"
               f"Execução: {_fmt(pt['exec'])} · Busca: {_fmt(pt['fetch'])}<br>"
               f"{pt['result']}<br><span style='color:gray'>{sql}</span>")
        QToolTip.showText(ev.globalPosition().toPoint(), tip, self)

    def leaveEvent(self, _):
        self.hover = -1
        self.update()


class PerfChart(QWidget):
    """Painel com o histórico de tempos das execuções da aba."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._n = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        head = QHBoxLayout()
        title = QLabel("Performance")
        title.setStyleSheet("font-weight:bold;")
        self.stats = QLabel()
        self.stats.setStyleSheet(f"color:{theme.c('muted')};")
        clear = QPushButton("Limpar")
        clear.setFlat(True)
        clear.clicked.connect(self.clear)
        head.addWidget(title)
        head.addWidget(self.stats, 1)
        head.addWidget(clear)
        lay.addLayout(head)
        self.canvas = _Canvas(self)
        lay.addWidget(self.canvas, 1)

    def add(self, sql: str, exec_time: float, fetch_time: float, ok: bool, result: str):
        self._n += 1
        pts = self.canvas.points
        pts.append(dict(n=self._n, sql=sql, exec=max(0.0, exec_time), fetch=max(0.0, fetch_time), ok=ok,
                        result=result, ts=datetime.datetime.now().strftime("%H:%M:%S")))
        del pts[:-MAX_POINTS]
        self._update_stats()
        self.canvas.update()

    def clear(self):
        self.canvas.points.clear()
        self.canvas.hover = -1
        self._update_stats()
        self.canvas.update()

    def _update_stats(self):
        totals = [p["exec"] + p["fetch"] for p in self.canvas.points if p["ok"]]
        if not totals:
            self.stats.setText("")
            return
        self.stats.setText(f"  mín {_fmt(min(totals))} · méd {_fmt(sum(totals) / len(totals))}"
                           f" · máx {_fmt(max(totals))}")
