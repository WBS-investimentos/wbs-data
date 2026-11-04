"""Grade de resultados: modelo, cópia, exportação e visualização de valores."""
from __future__ import annotations

import csv
import datetime
import decimal

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QPlainTextEdit, QPushButton, QTableView, QVBoxLayout, QWidget,
                               QAbstractItemView, QHeaderView)

from . import theme
from .worker import to_display

_NUMERIC = (int, float, decimal.Decimal)


class ResultModel(QAbstractTableModel):
    fetch_needed = Signal()   # a rolagem chegou ao fim e o cursor ainda tem linhas

    def __init__(self, columns, rows, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows = list(rows)
        self._row_text: list[str] = []
        self.has_more = False
        self.loading = False

    # A QTableView chama estes dois sozinha quando a barra de rolagem chega ao fim.
    def canFetchMore(self, parent=QModelIndex()):
        return not parent.isValid() and self.has_more and not self.loading

    def fetchMore(self, parent=QModelIndex()):
        if self.canFetchMore(parent):
            self.loading = True
            self.fetch_needed.emit()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def append(self, rows):
        if not rows:
            return
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(rows) - 1)
        self.rows.extend(rows)
        self.endInsertRows()

    def row_text(self, row) -> str:
        """Texto da linha inteira (minúsculo) para o filtro; montado sob demanda e guardado."""
        cache = self._row_text
        while len(cache) <= row:
            r = self.rows[len(cache)]
            cache.append("\x1f".join("" if v is None else to_display(v) for v in r).lower())
        return cache[row]

    def sort(self, column, order=Qt.AscendingOrder):
        """Ordena em Python com uma chave (rápido), em vez de comparar célula a célula via Qt."""
        if not 0 <= column < len(self.columns):
            return

        def key(r):
            v = r[column]
            if v is None:
                return (0, 0, "")
            if isinstance(v, _NUMERIC) and not isinstance(v, bool):
                return (1, float(v), "")
            if isinstance(v, (datetime.date, datetime.time)):
                return (1, 0, v.isoformat())
            return (1, 0, to_display(v))

        self.layoutAboutToBeChanged.emit()
        try:
            self.rows.sort(key=key, reverse=order == Qt.DescendingOrder)
        except TypeError:
            self.rows.sort(key=lambda r: to_display(r[column]), reverse=order == Qt.DescendingOrder)
        self._row_text = []
        self.layoutChanged.emit()

    def raw(self, row, col):
        return self.rows[row][col]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self.rows[index.row()][index.column()]
        if role == Qt.DisplayRole:
            s = to_display(v)
            if isinstance(v, str) and ("\n" in s or len(s) > 300):
                s = s.replace("\r", "").replace("\n", " ⏎ ")[:300]
            return s
        if role == Qt.UserRole:  # ordenação
            if v is None:
                return None
            if isinstance(v, _NUMERIC) and not isinstance(v, bool):
                return float(v)
            if isinstance(v, (datetime.date, datetime.time)):
                return v.isoformat()
            return to_display(v)
        if role == Qt.ForegroundRole and v is None:
            return QBrush(QColor(theme.c("null")))
        if role == Qt.FontRole and v is None:
            f = QFont()
            f.setItalic(True)
            return f
        if role == Qt.TextAlignmentRole and isinstance(v, _NUMERIC) and not isinstance(v, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and isinstance(v, str) and len(v) > 60:
            return v[:2000]
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.columns[section]["name"]
            if role == Qt.ToolTipRole:
                c = self.columns[section]
                return f"{c['name']}  ({c.get('type', '')})"
        elif role == Qt.DisplayRole:
            return str(section + 1)
        return None


class _Proxy(QSortFilterProxyModel):
    """Só filtra. A ordenação vai para o modelo (uma chamada Python, não milhões)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.needle = ""

    def sort(self, column, order=Qt.AscendingOrder):
        self.sourceModel().sort(column, order)

    def set_needle(self, text):
        self.needle = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        return not self.needle or self.needle in self.sourceModel().row_text(row)


def sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, _NUMERIC):
        return to_display(v)
    if isinstance(v, bytes):
        return "x'" + v.hex() + "'"
    return "'" + to_display(v).replace("'", "''") + "'"


class ValueDialog(QDialog):
    def __init__(self, column, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Valor de {column}")
        self.resize(700, 450)
        lay = QVBoxLayout(self)
        ed = QPlainTextEdit()
        if isinstance(value, bytes):
            try:
                text = value.decode("utf-8")
            except UnicodeDecodeError:
                text = value.hex(" ", 16)
                text = "\n".join(text[i:i + 48] for i in range(0, len(text), 48))
        else:
            text = "NULL" if value is None else to_display(value)
        ed.setPlainText(text)
        ed.setReadOnly(True)
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class ResultView(QWidget):
    fetch_more_requested = Signal()   # próxima página (disparado pela rolagem)

    def __init__(self, columns, rows, has_more, table_hint="TABELA", parent=None):
        super().__init__(parent)
        self.table_hint = table_hint
        self.model = ResultModel(columns, rows, self)
        self.proxy = _Proxy(self)
        self.proxy.setSourceModel(self.model)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filtrar resultado…")
        self.filter.setClearButtonEnabled(True)
        # filtra só quando a pessoa para de digitar
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(300)
        self._filter_timer.timeout.connect(lambda: self.proxy.set_needle(self.filter.text()))
        self.filter.textChanged.connect(lambda _: self._filter_timer.start())
        self.info = QLabel()
        self.model.fetch_needed.connect(self._on_fetch_needed)
        btn_csv = QPushButton("Exportar CSV")
        btn_csv.clicked.connect(self.export_csv)
        bar.addWidget(self.filter, 1)
        bar.addWidget(self.info)
        bar.addWidget(btn_csv)
        lay.addLayout(bar)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(-1, Qt.AscendingOrder)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.verticalHeader().setDefaultSectionSize(self.view.fontMetrics().height() + 6)
        self.view.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)   # não mede linha a linha
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.doubleClicked.connect(self._show_value)
        lay.addWidget(self.view)

        copy = QAction("Copiar", self.view)
        copy.setShortcut(QKeySequence.Copy)
        copy.setShortcutContext(Qt.WidgetShortcut)
        copy.triggered.connect(lambda: self.copy(False))
        self.view.addAction(copy)

        self.set_has_more(has_more)
        self._resize_columns()

    SAMPLE_ROWS = 40

    def _resize_columns(self):
        # resizeColumnsToContents() consulta TODAS as células (806 colunas x 1000 linhas = 122 s
        # travado). Aqui a largura vem do cabeçalho + uma amostra das primeiras linhas.
        fm = self.view.fontMetrics()
        hfm = self.view.horizontalHeader().fontMetrics()
        sample = self.model.rows[:self.SAMPLE_ROWS]
        for c, col in enumerate(self.model.columns):
            w = hfm.horizontalAdvance(col["name"]) + 28
            for r in sample:
                v = r[c]
                if v is not None:
                    w = max(w, fm.horizontalAdvance(to_display(v)[:60]) + 16)
            self.view.setColumnWidth(c, max(48, min(w, 400)))

    @property
    def has_more(self):
        return self.model.has_more

    def set_has_more(self, has_more):
        self.model.has_more = has_more
        self.model.loading = False
        self._update_info()

    def _update_info(self):
        n = len(self.model.rows)
        if self.model.loading:
            self.info.setText(f"{n} linha(s) — carregando mais…")
        elif self.model.has_more:
            self.info.setText(f"{n} linha(s) — role até o fim para carregar mais")
        else:
            self.info.setText(f"{n} linha(s)")

    def _on_fetch_needed(self):
        self._update_info()
        self.fetch_more_requested.emit()

    def append_rows(self, rows, has_more):
        self.model.append(rows)
        self.set_has_more(has_more)

    def _selected(self):
        idx = self.view.selectionModel().selectedIndexes()
        if not idx:
            return [], []
        src = [self.proxy.mapToSource(i) for i in idx]
        rows = sorted({i.row() for i in src})
        cols = sorted({i.column() for i in src})
        return rows, cols

    def copy(self, header=False):
        rows, cols = self._selected()
        if not rows:
            return
        lines = []
        if header:
            lines.append("\t".join(self.model.columns[c]["name"] for c in cols))
        for r in rows:
            vals = []
            for c in cols:
                v = self.model.raw(r, c)
                vals.append("" if v is None else to_display(v).replace("\t", " ").replace("\n", " "))
            lines.append("\t".join(vals))
        QGuiApplication.clipboard().setText("\n".join(lines))

    def copy_insert(self):
        rows, _ = self._selected()
        cols = list(range(self.model.columnCount()))
        names = ", ".join(self.model.columns[c]["name"] for c in cols)
        out = [f"INSERT INTO {self.table_hint} ({names}) VALUES ("
               + ", ".join(sql_literal(self.model.raw(r, c)) for c in cols) + ");" for r in rows]
        QGuiApplication.clipboard().setText("\n".join(out))

    def copy_in_list(self):
        rows, cols = self._selected()
        if cols:
            vals = sorted({sql_literal(self.model.raw(r, cols[0])) for r in rows})
            QGuiApplication.clipboard().setText("(" + ", ".join(vals) + ")")

    def _menu(self, pos):
        m = QMenu(self)
        m.addAction("Copiar", lambda: self.copy(False))
        m.addAction("Copiar com cabeçalho", lambda: self.copy(True))
        m.addAction("Copiar como INSERT", self.copy_insert)
        m.addAction("Copiar valores como lista IN (...)", self.copy_in_list)
        m.addSeparator()
        m.addAction("Ver valor…", lambda: self._show_value(self.view.currentIndex()))
        m.addAction("Exportar CSV…", self.export_csv)
        m.exec(self.view.viewport().mapToGlobal(pos))

    def _show_value(self, index):
        if not index.isValid():
            return
        src = self.proxy.mapToSource(index)
        ValueDialog(self.model.columns[src.column()]["name"], self.model.raw(src.row(), src.column()), self).exec()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar CSV", "resultado.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([c["name"] for c in self.model.columns])
            for r in range(self.proxy.rowCount()):
                sr = self.proxy.mapToSource(self.proxy.index(r, 0)).row()
                w.writerow(["" if v is None else to_display(v) for v in self.model.rows[sr]])
