"""Editor de SQL: numeração de linhas, realce de sintaxe e autocompletar."""
from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt, QStringListModel
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QKeySequence, QPainter, QSyntaxHighlighter,
                           QTextCharFormat, QTextCursor, QTextFormat)
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit, QWidget

from . import theme

KEYWORDS = """
ADD ALL ALTER AND ANY AS ASC AUTONOMOUS BEGIN BETWEEN BIGINT BLOB BLOCK BOOLEAN BY CASCADE CASE CAST CHAR
CHARACTER CHECK COALESCE COLLATE COLUMN COMMIT COMPUTED CONSTRAINT CONTAINING COUNT CREATE CROSS CURRENT_DATE
CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER CURSOR DATABASE DATE DECIMAL DECLARE DEFAULT DELETE DESC
DISTINCT DO DOMAIN DOUBLE DROP ELSE END ENTRY_POINT EXCEPTION EXECUTE EXISTS EXIT EXTERNAL EXTRACT FALSE
FETCH FILTER FIRST FLOAT FOR FOREIGN FROM FULL FUNCTION GEN_ID GENERATOR GRANT GROUP HAVING IF IN INACTIVE
ACTIVE INDEX INNER INSERT INT INTEGER INTO IS JOIN KEY LEFT LIKE LIMIT LIST MATCHING MAX MERGE MIN NEXT
NOT NULL NULLIF NUMERIC OF OFFSET ON ONLY OR ORDER OUTER PACKAGE POSITION PRECISION PRIMARY PROCEDURE
RECREATE REFERENCES RETURNING RETURNS REVOKE RIGHT ROLE ROLLBACK ROWS ROW_NUMBER SAVEPOINT SCHEMA SELECT
SEQUENCE SET SKIP SMALLINT SOME STARTING SUBSTRING SUM SUSPEND TABLE TERM THEN TIME TIMESTAMP TO TOP
TRANSACTION TRIGGER TRIM TRUE TRUNCATE TYPE UNION UNIQUE UPDATE UPPER LOWER USING VALUE VALUES VARCHAR
VARIABLE VIEW WHEN WHERE WHILE WITH WORK REPLACE OVER PARTITION LEAD LAG ROWS RANGE PLAN SORT NATURAL
EXPLAIN SHOW USE AUTO_INCREMENT SERIAL RETURN LOOP RAISE NOTICE PERFORM LANGUAGE REPLACE DATEADD DATEDIFF
""".split()


_KW_SET = set(KEYWORDS)
_TABLE_CONTEXT = {"FROM", "JOIN", "INTO", "UPDATE", "TABLE"}
_NOT_ALIAS = {"WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "ON", "SET", "ORDER", "GROUP",
              "HAVING", "UNION", "VALUES", "AS", "USING", "NATURAL", "PLAN", "ROWS", "WITH", "LIMIT", "INTO",
              "SELECT", "FROM", "RETURNING", "MATCHING", "WINDOW", "FETCH", "OFFSET", "FOR"}
_ALIAS_RX = re.compile(r'(?:\bFROM\b|\bJOIN\b|\bUPDATE\b|\bINTO\b|,)\s*("?[\w$.]+"?)(?:\s+(?:AS\s+)?([\w$]+))?', re.I)

FONT_FAMILY = "JetBrains Mono"


def mono_font(size=11) -> QFont:
    if FONT_FAMILY in QFontDatabase.families():
        f = QFont(FONT_FAMILY)
    else:
        f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(theme.pt(size))
    return f


class SqlHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.set_colors()
        self.tokens = re.compile(r"'(?:[^']|'')*'?|--[^\n]*|/\*|\"(?:[^\"]|\"\")*\"?")

    def set_colors(self):
        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            f.setFontItalic(italic)
            return f

        self.f_kw = fmt(theme.c("kw"), bold=True)
        self.f_str = fmt(theme.c("str"))
        self.f_num = fmt(theme.c("num"))
        self.f_com = fmt(theme.c("com"), italic=True)
        self.f_param = fmt(theme.c("param"))
        kw = "|".join(sorted(set(KEYWORDS), key=len, reverse=True))
        self.rules = [
            (re.compile(rf"\b(?:{kw})\b", re.I), self.f_kw),
            (re.compile(r"\b\d+(?:\.\d+)?\b"), self.f_num),
            (re.compile(r"(?<![\w:]):\w+|\?"), self.f_param),
        ]

    def highlightBlock(self, text):
        for rx, f in self.rules:
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), f)
        # strings e comentários têm prioridade; /* */ pode atravessar linhas
        i = 0
        self.setCurrentBlockState(0)
        if self.previousBlockState() == 1:
            e = text.find("*/")
            if e < 0:
                self.setFormat(0, len(text), self.f_com)
                self.setCurrentBlockState(1)
                return
            self.setFormat(0, e + 2, self.f_com)
            i = e + 2
        while True:
            m = self.tokens.search(text, i)
            if not m:
                break
            tok = m.group(0)
            if tok == "/*":
                e = text.find("*/", m.end())
                if e < 0:
                    self.setFormat(m.start(), len(text) - m.start(), self.f_com)
                    self.setCurrentBlockState(1)
                    return
                self.setFormat(m.start(), e + 2 - m.start(), self.f_com)
                i = e + 2
                continue
            if tok.startswith("--"):
                self.setFormat(m.start(), len(tok), self.f_com)
            elif tok.startswith("'"):
                self.setFormat(m.start(), len(tok), self.f_str)
            else:
                self.setFormat(m.start(), len(tok), QTextCharFormat())
            i = m.end()


class _LineArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_area_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_area(event)


class SqlEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.error_range: tuple[int, int] | None = None
        self._link_range: tuple[int, int] | None = None  # nome sublinhado com Ctrl pressionado
        self._search_ranges: list[tuple[int, int]] = []  # ocorrências da barra de localizar
        self._search_current = -1
        self.escape_handler = None                        # callable() -> bool: Esc fecha a barra de localizar
        self.setFont(mono_font())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.highlighter = SqlHighlighter(self.document())
        self.line_area = _LineArea(self)
        self.blockCountChanged.connect(lambda _: self._update_margin())
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_line)
        self.verticalScrollBar().valueChanged.connect(lambda _: self._search_ranges and self._highlight_line())
        self._update_margin()
        self._highlight_line()

        # autocompletar: palavras-chave, tabelas/views, outros objetos e colunas
        self.table_names: list[str] = []
        self.other_names: list[str] = []
        self.columns_cache: dict[str, list[str]] = {}   # TABELA (maiúsc.) -> colunas
        self.columns_provider = None                     # callable(tabela): busca colunas em background
        self.structure_handler = None                    # callable(nome): Ctrl+clique mostra a estrutura
        self.std_sql_handler = None                      # callable(tipo, sugestão): menu "SQL padrão"
        self.setMouseTracking(True)
        self._requested: set[str] = set()
        self.completer = QCompleter(self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setFilterMode(Qt.MatchStartsWith)
        self.completer.setMaxVisibleItems(14)
        self.completer.activated.connect(self._insert_completion)
        self._model = QStringListModel(self.completer)
        self.completer.setModel(self._model)

    # ---------------- autocompletar ----------------
    def set_objects(self, objects: dict[str, list[str]]):
        """Recebe {categoria: [nomes]} vindo dos metadados do banco."""
        short = lambda n: n.split(".")[-1]
        self.table_names = sorted({short(n) for cat in ("Tabelas", "Views") for n in objects.get(cat, [])}, key=str.upper)
        self.other_names = sorted({short(n) for cat, lst in objects.items() if cat not in ("Tabelas", "Views")
                                   for n in lst}, key=str.upper)
        self._requested.clear()

    def set_columns(self, table: str, columns: list[str]):
        key = table.split(".")[-1].upper()
        self.columns_cache[key] = columns
        # se o usuário está esperando as colunas desta tabela, mostra agora
        if self.hasFocus() and self._dot_context() and self._resolve(self._dot_context()[0]) == key:
            self._show_completer(force=True)

    def clear_completion_cache(self):
        self.table_names, self.other_names = [], []
        self.columns_cache.clear()
        self._requested.clear()

    def _line_before_cursor(self) -> str:
        c = self.textCursor()
        return c.block().text()[:c.positionInBlock()]

    def _word_under_cursor(self) -> str:
        m = re.search(r"[\w$]+$", self._line_before_cursor())
        return m.group(0) if m else ""

    def _dot_context(self):
        """('QUALIFICADOR', prefixo) quando o cursor está depois de 'algo.'"""
        m = re.search(r"([\w$]+)\.([\w$]*)$", self._line_before_cursor())
        return (m.group(1), m.group(2)) if m else None

    def _aliases(self) -> dict[str, str]:
        tables = {t.upper() for t in self.table_names}
        out = {}
        for m in _ALIAS_RX.finditer(self.toPlainText()):
            table = m.group(1).strip('"').split(".")[-1].upper()
            if table not in tables:
                continue
            out[table] = table
            alias = (m.group(2) or "").upper()
            if alias and alias not in _NOT_ALIAS:
                out[alias] = table
        return out

    def _resolve(self, qualifier: str) -> str | None:
        q = qualifier.upper()
        if q in {t.upper() for t in self.table_names}:
            return q
        return self._aliases().get(q)

    def _want_columns(self, table: str) -> list[str] | None:
        cols = self.columns_cache.get(table)
        if cols is None and self.columns_provider and table not in self._requested:
            self._requested.add(table)
            self.columns_provider(table)
        return cols

    def _insert_completion(self, word):
        c = self.textCursor()
        prefix = self._word_under_cursor()
        c.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
        if prefix and prefix.islower() and word.isupper() and word.upper() in _KW_SET:
            word = word.lower()
        c.insertText(word)
        self.setTextCursor(c)

    def _show_completer(self, force=False):
        popup = self.completer.popup()
        prefix = self._word_under_cursor()
        dot = self._dot_context()
        if dot:                                     # TABELA. / alias.
            table = self._resolve(dot[0])
            words = self._want_columns(table) if table else None
            if not words:
                popup.hide()
                return
        else:
            before = self._line_before_cursor()[:len(self._line_before_cursor()) - len(prefix)]
            prev = re.search(r"([\w$]+)\s*$", before)
            prev = prev.group(1).upper() if prev else ""
            if prev in _TABLE_CONTEXT or re.search(r"\bFROM\b[^;]*,\s*$", before, re.I):
                words = self.table_names              # depois de FROM/JOIN/...: só tabelas
                if not force and len(prefix) < 1:
                    popup.hide()
                    return
            else:
                if not force and len(prefix) < 2:
                    popup.hide()
                    return
                cols = []
                for t in set(self._aliases().values()):   # colunas das tabelas usadas no texto
                    cols.extend(self._want_columns(t) or [])
                words = sorted(set(KEYWORDS) | set(self.table_names) | set(self.other_names) | set(cols),
                               key=str.upper)
        self._model.setStringList(words)
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0 or (
                self.completer.completionCount() == 1 and self.completer.currentCompletion().upper() == prefix.upper()):
            popup.hide()
            return
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        r = self.cursorRect()
        r.setWidth(min(500, popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 24))
        self.completer.complete(r)

    # ---------------- Ctrl+clique: estrutura da tabela ----------------
    def _name_at(self, pos):
        """(início, fim, nome) da tabela/view sob o ponto, ou None."""
        c = self.cursorForPosition(pos)
        block, col = c.block(), c.positionInBlock()
        for m in re.finditer(r'"?[\w$]+"?(?:\."?[\w$]+"?)?', block.text()):
            if not m.start() <= col <= m.end():
                continue
            # cursorForPosition aceita pontos além do fim da linha: exige o mouse em cima da palavra
            a, b = QTextCursor(block), QTextCursor(block)
            a.setPosition(block.position() + m.start())
            b.setPosition(block.position() + m.end())
            if not self.cursorRect(a).left() <= pos.x() <= self.cursorRect(b).right():
                return None
            name = m.group(0).replace('"', "")
            if name.split(".")[-1].upper() in {t.upper() for t in self.table_names}:
                return a.position(), b.position(), name
            return None
        return None

    def _set_link(self, rng):
        if rng != self._link_range:
            self._link_range = rng
            self.viewport().setCursor(Qt.PointingHandCursor if rng else Qt.IBeamCursor)
            self._highlight_line()

    def _update_link(self, pos, modifiers):
        hit = self._name_at(pos) if modifiers & Qt.ControlModifier and self.structure_handler else None
        self._set_link(hit[:2] if hit else None)

    def mouseMoveEvent(self, e):
        self._update_link(e.position().toPoint(), e.modifiers())
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.modifiers() & Qt.ControlModifier and self.structure_handler:
            hit = self._name_at(e.position().toPoint())
            if hit:
                self.structure_handler(hit[2])
                e.accept()
                return
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        menu = self.createStandardContextMenu(e.pos())
        if self.std_sql_handler:
            sel = self.textCursor().selectedText().strip()
            hit = self._name_at(e.pos())
            guess = sel if sel and "\u2029" not in sel and len(sel) < 128 else (hit[2] if hit else "")
            menu.addSeparator()
            sub = menu.addMenu(theme.icon("code", "icon"), "SQL padrão")
            sub.addAction(theme.icon("table", "icon"), "Tabela existe?…",
                          lambda: self.std_sql_handler("table", guess))
            sub.addAction(theme.icon("columns", "icon"), "Coluna existe?…",
                          lambda: self.std_sql_handler("column", guess))
        menu.exec(e.globalPos())
        menu.deleteLater()

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Control:
            self._set_link(None)
        super().keyReleaseEvent(e)

    def leaveEvent(self, e):
        self._set_link(None)
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Control:
            self._update_link(self.viewport().mapFromGlobal(self.cursor().pos()), Qt.ControlModifier)
        popup = self.completer.popup()
        if popup.isVisible() and e.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Tab, Qt.Key_Escape):
            if e.key() == Qt.Key_Escape:
                popup.hide()
                return
            e.ignore()
            return
        if e.key() == Qt.Key_Space and e.modifiers() & Qt.ControlModifier:
            self._show_completer(force=True)
            return
        if e.matches(QKeySequence.StandardKey.Find):
            e.ignore()
            return
        if e.key() == Qt.Key_Escape and not e.modifiers() and self.escape_handler and self.escape_handler():
            return
        if e.key() == Qt.Key_Slash and e.modifiers() & Qt.ControlModifier:
            self.toggle_comment()
            return
        if e.key() == Qt.Key_Tab and not e.modifiers():
            self.insertPlainText("    ")
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers():
            indent = re.match(r"[ \t]*", self.textCursor().block().text()).group(0)
            super().keyPressEvent(e)
            self.insertPlainText(indent)
            return
        super().keyPressEvent(e)
        if e.text() and (e.text().isalnum() or e.text() in "_$."):
            self._show_completer()
        elif popup.isVisible():
            popup.hide()

    def toggle_comment(self):
        c = self.textCursor()
        doc = self.document()
        start = doc.findBlock(c.selectionStart())
        end = doc.findBlock(c.selectionEnd() - (1 if c.hasSelection() else 0))
        blocks = []
        b = start
        while b.isValid():
            blocks.append(b)
            if b == end:
                break
            b = b.next()
        all_commented = all(b.text().lstrip().startswith("--") or not b.text().strip() for b in blocks)
        c.beginEditBlock()
        for b in blocks:
            t = b.text()
            bc = QTextCursor(b)
            if all_commented:
                k = t.find("--")
                if k >= 0:
                    bc.setPosition(b.position() + k)
                    bc.setPosition(b.position() + k + (3 if t[k + 2:k + 3] == " " else 2), QTextCursor.KeepAnchor)
                    bc.removeSelectedText()
            elif t.strip():
                bc.insertText("-- ")
        c.endEditBlock()

    # ---------------- margem / destaque ----------------
    def line_area_width(self):
        digits = max(3, len(str(self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self):
        self.setViewportMargins(self.line_area_width(), 0, 0, 0)

    def _on_update_request(self, rect, dy):
        if dy:
            self.line_area.scroll(0, dy)
        else:
            self.line_area.update(0, rect.y(), self.line_area.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.line_area.setGeometry(QRect(cr.left(), cr.top(), self.line_area_width(), cr.height()))

    def paint_line_area(self, event):
        p = QPainter(self.line_area)
        p.fillRect(event.rect(), QColor(theme.c("gutter_bg")))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        cur = self.textCursor().blockNumber()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(QColor(theme.c("gutter_cur") if num == cur else theme.c("gutter_fg")))
                p.drawText(0, top, self.line_area.width() - 5, self.fontMetrics().height(), Qt.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1

    def _highlight_line(self):
        sels = []
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(QColor(theme.c("line_bg")))
        line.format.setProperty(QTextFormat.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        sels.append(line)
        if self.error_range:
            err = QTextEdit.ExtraSelection()
            err.format.setUnderlineStyle(QTextCharFormat.WaveUnderline)
            err.format.setUnderlineColor(QColor(theme.c("red")))
            err.format.setBackground(QColor(theme.c("err_bg")))
            c = QTextCursor(self.document())
            c.setPosition(min(self.error_range[0], len(self.toPlainText())))
            c.setPosition(min(self.error_range[1], len(self.toPlainText())), QTextCursor.KeepAnchor)
            err.cursor = c
            sels.append(err)
        if self._search_ranges:
            n = len(self.toPlainText())
            first = self.firstVisibleBlock().position()
            last = self.cursorForPosition(self.viewport().rect().bottomRight()).block()
            last = last.position() + last.length() if last.isValid() else n
            for i, (s, e) in enumerate(self._search_ranges):
                # só as visíveis (e a atual): milhares de ExtraSelections deixam o editor lento
                if i != self._search_current and (e < first or s > last):
                    continue
                m = QTextEdit.ExtraSelection()
                m.format.setBackground(theme.qcolor(theme.skorpa("blueColorAlpha25")))
                if i == self._search_current:
                    m.format.setUnderlineStyle(QTextCharFormat.SingleUnderline)
                    m.format.setUnderlineColor(QColor(theme.c("orange")))
                    m.format.setFontUnderline(True)
                c = QTextCursor(self.document())
                c.setPosition(min(s, n))
                c.setPosition(min(e, n), QTextCursor.KeepAnchor)
                m.cursor = c
                sels.append(m)
        if self._link_range:
            link = QTextEdit.ExtraSelection()
            link.format.setFontUnderline(True)
            link.format.setForeground(QColor(theme.c("blue")))
            c = QTextCursor(self.document())
            c.setPosition(self._link_range[0])
            c.setPosition(self._link_range[1], QTextCursor.KeepAnchor)
            link.cursor = c
            sels.append(link)
        self.setExtraSelections(sels)

    def apply_theme(self):
        self.highlighter.set_colors()
        self.highlighter.rehighlight()
        self._highlight_line()
        self.line_area.update()

    def set_search_highlights(self, ranges, current=-1):
        self._search_ranges = list(ranges)
        self._search_current = current
        self._highlight_line()

    def mark_error(self, start, end):
        self.error_range = (start, end)
        self._highlight_line()

    def clear_error(self):
        if self.error_range:
            self.error_range = None
            self._highlight_line()

    def select_range(self, start, end):
        c = self.textCursor()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        self.setTextCursor(c)
