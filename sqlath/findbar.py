"""Barra de localizar/substituir do editor (Ctrl+F / Ctrl+H), flutuando no canto do editor.

Busca enquanto digita, destaca todas as ocorrências, mostra "3 de 12" e aceita
maiúsculas/minúsculas, palavra inteira e expressão regular. Enter/F3 = próxima,
Shift+Enter/Shift+F3 = anterior, Esc fecha.
"""
from __future__ import annotations

import re
import sys

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QTextCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from . import theme

MAX_MATCHES = 10_000          # acima disso só conta, para não travar documentos enormes
MOD = "⌘" if sys.platform == "darwin" else "Ctrl+"
ALT = "⌥" if sys.platform == "darwin" else "Alt+"
# Cmd+H esconde o app no macOS: lá o substituir fica em Cmd+Option+F
REPLACE_KEY = "Ctrl+Alt+F" if sys.platform == "darwin" else "Ctrl+H"


def _utf16_map(text: str):
    """Posição Python -> posição do QTextDocument (UTF-16). None quando são iguais (sem emoji etc.)."""
    if not any(ord(ch) > 0xFFFF for ch in text):
        return None
    out, acc = [], 0
    for ch in text:
        out.append(acc)
        acc += 2 if ord(ch) > 0xFFFF else 1
    out.append(acc)
    return out


class FindBar(QFrame):
    def __init__(self, host: QWidget, editor_getter):
        """`host` é o widget sobre o qual a barra flutua; `editor_getter()` devolve o editor atual."""
        super().__init__(host)
        self.host = host
        self.editor_getter = editor_getter
        self._editor = None
        self.matches: list[tuple[int, int]] = []
        self._found: list[re.Match] = []     # casamentos (para \1, \2… no substituir)
        self._sel_range = (0, 0)
        self.current = -1
        self.total = 0
        self.setObjectName("findbar")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(lambda: self.search(keep_position=True))

        def tbtn(tip, slot=None, icon=None, text=None, checkable=False):
            b = QToolButton()
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.NoFocus)
            b.setIconSize(QSize(16, 16))
            if text:
                b.setText(text)
            if icon:
                b._icon = icon
            if slot:
                (b.toggled if checkable else b.clicked).connect(slot)
            return b

        self.b_expand = tbtn(f"Mostrar/ocultar substituir ({REPLACE_KEY.replace('Ctrl+', MOD).replace('Alt+', ALT)})",
                             lambda: self.set_replace(not self.replace_row.isVisible()), "chevron-right")
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Localizar")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.setMinimumWidth(220)
        self.find_edit.textChanged.connect(lambda _: self._timer.start())
        self.b_case = tbtn("Diferenciar maiúsculas/minúsculas", self._opt_changed, text="Aa", checkable=True)
        self.b_word = tbtn("Palavra inteira", self._opt_changed, text="ab", checkable=True)
        self.b_regex = tbtn("Expressão regular", self._opt_changed, text=".*", checkable=True)
        self.b_sel = tbtn("Só na seleção", self._opt_changed, "list-search", checkable=True)
        self.count = QLabel()
        self.count.setMinimumWidth(88)
        self.count.setAlignment(Qt.AlignCenter)
        self.b_prev = tbtn("Anterior (Shift+F3 / Shift+Enter)", self.find_prev, "arrow-up")
        self.b_next = tbtn("Próxima (F3 / Enter)", self.find_next, "arrow-down")
        self.b_close = tbtn("Fechar (Esc)", self.close_bar, "x")

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Substituir")
        self.replace_edit.setToolTip("Com expressão regular, \\1, \\2… inserem os grupos")
        self.b_rep = tbtn("Substituir esta (Enter no campo)", self.replace_one, text="Substituir")
        self.b_rep_all = tbtn(f"Substituir todas ({MOD}{ALT}Enter)", self.replace_all, text="Todas")

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(2)
        for w in (self.find_edit, self.b_case, self.b_word, self.b_regex, self.b_sel, self.count,
                  self.b_prev, self.b_next, self.b_close):
            row1.addWidget(w)
        row1.setStretch(0, 1)
        self.replace_row = QWidget()
        row2 = QHBoxLayout(self.replace_row)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(2)
        row2.addWidget(self.replace_edit, 1)
        row2.addWidget(self.b_rep)
        row2.addWidget(self.b_rep_all)
        self.replace_row.hide()

        rows = QVBoxLayout()
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(4)
        rows.addLayout(row1)
        rows.addWidget(self.replace_row)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 4, 6, 4)
        outer.setSpacing(2)
        outer.addWidget(self.b_expand, 0, Qt.AlignTop)
        outer.addLayout(rows, 1)

        for e in (self.find_edit, self.replace_edit):
            e.installEventFilter(self)
        host.installEventFilter(self)
        self.apply_theme()
        self.hide()

    # ------------------------------------------------------------ aparência
    def apply_theme(self):
        k = theme.skorpa
        self.setStyleSheet(f"""
            #findbar {{ background: {k("cardBg")}; border: 1px solid {k("cardBgHover")}; border-radius: 8px; }}
            #findbar QToolButton {{ border: 1px solid transparent; border-radius: 4px; padding: 2px 4px;
                                    font-family: monospace; }}
            #findbar QToolButton:hover {{ background: {k("cardBgHover")}; }}
            #findbar QToolButton:checked {{ background: {k("blueColorAlpha25")}; border-color: {k("blueColor")};
                                            color: {k("blueColor")}; }}
            #findbar QLineEdit {{ background: {k("blackColor")}; }}
            #findbar QLineEdit[bad="true"] {{ border-color: {k("negativeColor")}; }}
        """)
        for b in self.findChildren(QToolButton):
            if hasattr(b, "_icon"):
                b.setIcon(theme.icon(b._icon, "icon"))
        self._update_count()
        if self._editor:
            self._editor.viewport().update()

    def _place(self):
        h = self.host
        w = min(max(self.sizeHint().width(), 520), max(200, h.width() - 24))
        top = 0
        tb = getattr(h, "tabBar", None)
        if tb:
            top = tb().height()
        self.setGeometry(h.width() - w - 18, top + 4, w, self.sizeHint().height())
        self.raise_()

    def eventFilter(self, obj, e):
        if obj is self.host and e.type() == QEvent.Resize and self.isVisible():
            self._place()
        elif obj in (self.find_edit, self.replace_edit) and e.type() == QEvent.KeyPress:
            key, mods = e.key(), e.modifiers()
            if key == Qt.Key_Escape:
                self.close_bar()
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if obj is self.replace_edit:
                    if mods & Qt.ControlModifier and mods & Qt.AltModifier:
                        self.replace_all()
                    else:
                        self.replace_one()
                elif mods & Qt.ShiftModifier:
                    self.find_prev()
                else:
                    self.find_next()
                return True
            if key == Qt.Key_Tab and obj is self.find_edit and self.replace_row.isVisible():
                self.replace_edit.setFocus()
                self.replace_edit.selectAll()
                return True
            if key == Qt.Key_Backtab and obj is self.replace_edit:
                self.find_edit.setFocus()
                return True
        return super().eventFilter(obj, e)

    # ------------------------------------------------------------ abrir / fechar
    def open(self, replace=False):
        ed = self.editor_getter()
        if ed is None:
            return
        sel = ed.textCursor().selectedText()
        multi = " " in sel
        if self.b_sel.isChecked() and not multi:
            self.b_sel.blockSignals(True)
            self.b_sel.setChecked(False)
            self.b_sel.blockSignals(False)
        if multi:                                          # várias linhas selecionadas: busca dentro delas
            self.b_sel.setChecked(True)
            self._sel_range = (ed.textCursor().selectionStart(), ed.textCursor().selectionEnd())
        elif sel and len(sel) < 200:
            self.find_edit.blockSignals(True)
            self.find_edit.setText(re.escape(sel) if self.b_regex.isChecked() else sel)
            self.find_edit.blockSignals(False)
        self.set_replace(replace or self.replace_row.isVisible() and self.isVisible())
        self.show()
        self._place()
        self._attach(ed)
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self.search(keep_position=True)

    def close_bar(self):
        self._timer.stop()
        ed = self._editor
        self._detach()
        self.hide()
        if ed:
            ed.setFocus()

    def set_replace(self, on: bool):
        self.replace_row.setVisible(on)
        self.b_expand._icon = "chevron-down" if on else "chevron-right"
        self.b_expand.setIcon(theme.icon(self.b_expand._icon, "icon"))
        if self.isVisible():
            self._place()
            if on:
                self.replace_edit.setFocus()
                self.replace_edit.selectAll()

    def editor_changed(self):
        """Sub-aba trocada: a barra passa a buscar no novo editor."""
        if self.isVisible():
            self._attach(self.editor_getter())
            self.search(keep_position=True)

    def _attach(self, ed):
        if ed is self._editor:
            return
        self._detach()
        self._editor = ed
        if ed is not None:
            ed.textChanged.connect(self._timer.start)

    def _detach(self):
        ed = self._editor
        if ed is None:
            return
        try:
            ed.textChanged.disconnect(self._timer.start)
        except (RuntimeError, TypeError):
            pass
        ed.set_search_highlights([], -1)
        self._editor = None

    # ------------------------------------------------------------ busca
    def _opt_changed(self, *_):
        ed = self._editor or self.editor_getter()
        if self.b_sel.isChecked() and ed is not None:
            c = ed.textCursor()
            self._sel_range = (c.selectionStart(), c.selectionEnd()) if c.hasSelection() else (0, 0)
        self.search(keep_position=True)

    def _pattern(self):
        text = self.find_edit.text()
        if not text:
            return None
        src = text if self.b_regex.isChecked() else re.escape(text)
        if self.b_word.isChecked():
            src = rf"(?<![\w$]){src}(?![\w$])"
        flags = re.MULTILINE | (0 if self.b_case.isChecked() else re.IGNORECASE)
        return re.compile(src, flags)

    def search(self, keep_position=False):
        ed = self._editor
        if ed is None or not self.isVisible():
            return
        self.find_edit.setProperty("bad", False)
        self.matches, self._found, self.total, self.current = [], [], 0, -1
        try:
            rx = self._pattern()
        except re.error:
            rx = None
            self.find_edit.setProperty("bad", True)
        self.find_edit.style().unpolish(self.find_edit)
        self.find_edit.style().polish(self.find_edit)
        if rx is None:
            ed.set_search_highlights([], -1)
            self._update_count(bad=bool(self.find_edit.property("bad")))
            return
        text = ed.toPlainText().replace(" ", "\n")
        conv = _utf16_map(text)
        lo, hi = 0, len(text)
        if self.b_sel.isChecked():
            a, b = self._sel_range
            if conv:   # a seleção vem em UTF-16: volta para posições Python
                inv = {q: p for p, q in enumerate(conv)}
                a, b = inv.get(a, a), inv.get(b, b)
            lo, hi = (a, b) if b > a else (0, 0)
        for m in rx.finditer(text, lo, hi):
            if m.end() == m.start():     # casamento vazio (ex.: "a*") não conta
                continue
            self.total += 1
            if len(self.matches) < MAX_MATCHES:
                s, e = (conv[m.start()], conv[m.end()]) if conv else (m.start(), m.end())
                self.matches.append((s, e))
                self._found.append(m)
        if self.matches:
            c = ed.textCursor()
            # mantém a ocorrência sob o cursor (ou a próxima depois do início da seleção)
            pos = c.selectionStart() if keep_position else c.position()
            self.current = next((i for i, (s, _) in enumerate(self.matches) if s >= pos), 0)
            self._go(self.current, move_cursor=keep_position and self.find_edit.hasFocus())
        else:
            ed.set_search_highlights([], -1)
        self._update_count()

    def _go(self, i, move_cursor=True):
        ed = self._editor
        if ed is None or not self.matches:
            return
        self.current = i % len(self.matches)
        s, e = self.matches[self.current]
        if move_cursor:
            c = ed.textCursor()
            c.setPosition(s)
            c.setPosition(e, QTextCursor.KeepAnchor)
            ed.setTextCursor(c)
            if ed.viewport().rect().contains(ed.cursorRect()):
                ed.ensureCursorVisible()
            else:
                ed.centerCursor()
        ed.set_search_highlights(self.matches, self.current)
        self._update_count()

    def _step(self, delta):
        ed = self._editor
        if ed is None:
            return
        if self._timer.isActive():       # ainda digitando: atualiza antes de andar
            self._timer.stop()
            self.search(keep_position=True)
        if not self.matches:
            return
        c = ed.textCursor()
        s, e = self.matches[self.current] if 0 <= self.current < len(self.matches) else (-1, -1)
        on_match = c.selectionStart() == s and c.selectionEnd() == e
        if on_match:
            self._go(self.current + delta)
        else:   # cursor andou: parte da posição atual
            pos = c.position()
            if delta > 0:
                i = next((i for i, (ms, _) in enumerate(self.matches) if ms >= pos), 0)
            else:
                i = next((i for i in range(len(self.matches) - 1, -1, -1) if self.matches[i][1] <= c.selectionStart()),
                         len(self.matches) - 1)
            self._go(i)

    def _ensure_open(self) -> bool:
        """F3 com a barra fechada: reabre com o último termo (sem termo, abre para digitar)."""
        if self.isVisible():
            return True
        if not self.find_edit.text() or self.editor_getter() is None:
            self.open()
            return False
        self.show()
        self._place()
        self._attach(self.editor_getter())
        self.search()
        return True

    def find_next(self):
        if self._ensure_open():
            self._step(+1)

    def find_prev(self):
        if self._ensure_open():
            self._step(-1)

    def _update_count(self, bad=False):
        red, muted = theme.c("red"), theme.c("muted")
        if bad:
            self.count.setText("regex inválida")
            color = red
        elif not self.find_edit.text():
            self.count.setText("")
            color = muted
        elif not self.total:
            self.count.setText("Sem resultados")
            color = red
        else:
            n = f"{self.total:,}".replace(",", ".")
            cur = f"{self.current + 1:,}".replace(",", ".") if self.current >= 0 else "?"
            self.count.setText(f"{cur} de {n}")
            color = muted
        self.count.setStyleSheet(f"color: {color};")
        has = bool(self.matches)
        for b in (self.b_prev, self.b_next, self.b_rep, self.b_rep_all):
            b.setEnabled(has)

    # ------------------------------------------------------------ substituir
    def _replacement(self, i: int) -> str:
        rep = self.replace_edit.text()
        if not self.b_regex.isChecked():
            return rep
        try:
            return self._found[i].expand(rep)
        except (re.error, IndexError):
            return rep

    def replace_one(self):
        ed = self._editor
        if self._timer.isActive():
            self._timer.stop()
            self.search(keep_position=True)
        if ed is None or not self.matches:
            return
        c = ed.textCursor()
        s, e = self.matches[self.current]
        if not (c.selectionStart() == s and c.selectionEnd() == e):
            self._go(self.current)          # primeiro clique só mostra a ocorrência
            return
        c.insertText(self._replacement(self.current))
        ed.setTextCursor(c)
        self._timer.stop()
        self.search(keep_position=True)
        if self.matches:
            self._go(self.current)

    def replace_all(self):
        ed = self._editor
        if ed is None or not self.matches:
            return
        if self.total > len(self.matches):
            self.search()
        doc = ed.document()
        c = QTextCursor(doc)
        c.beginEditBlock()                    # um Ctrl+Z desfaz tudo
        for i in range(len(self.matches) - 1, -1, -1):
            s, e = self.matches[i]
            c.setPosition(s)
            c.setPosition(e, QTextCursor.KeepAnchor)
            c.insertText(self._replacement(i))
        c.endEditBlock()
        n = len(self.matches)
        self._timer.stop()
        self.search(keep_position=True)
        self.count.setText(f"{n} substituída{'s' if n != 1 else ''}")
