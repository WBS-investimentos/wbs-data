"""Janela principal: árvore de bancos, abas de SQL, resultados e histórico."""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QSpinBox,
                               QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QToolBar,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import __version__, theme
from .dialogs import ConnectionDialog, StructureDialog
from .drivers import get_driver
from .editor import SqlEditor
from .perfchart import PerfChart
from .results import ResultView
from .formatter import format_sql
from .sqlsplit import is_read_only, kind_of, leading_words, split_sql
from .storage import (CONFIG_DIR, ENV_COLORS, ENVIRONMENTS, SUB_TABS, ConnectionStore, History, load_workspace,
                      save_workspace, workspace_is_empty)
from .worker import Session

SESSION_FILE = CONFIG_DIR / "open_tabs.json"

SUB_ICONS = {"SELECT": ("eye", "green"), "INSERT": ("file-plus", "blue"), "UPDATE": ("pencil", "orange"),
             "DELETE": ("trash", "red"), "OUTROS": ("code", "icon")}
SUB_TIPS = {"SELECT": "Consultas (SELECT / WITH)", "INSERT": "INSERT, UPDATE OR INSERT, MERGE",
            "UPDATE": "UPDATE", "DELETE": "DELETE",
            "OUTROS": "CREATE, ALTER, DROP, procedures, EXECUTE BLOCK, scripts…"}

CAT_ICONS = {"Tabelas": "table", "Views": "eye", "Procedures": "code", "Triggers": "bolt",
             "Generators": "list-numbers", "Sequences": "list-numbers", "Domínios": "category",
             "Funções": "math-function", "Packages": "package", "Índices": "list-search"}
CAT_COLORS = {"Tabelas": "blue", "Views": "green", "Procedures": "orange", "Triggers": "yellow",
              "Funções": "orange", "Packages": "orange"}
MAX_RESULT_TABS = 20

R_KIND = Qt.UserRole
R_PID = Qt.UserRole + 1
R_CAT = Qt.UserRole + 2
R_NAME = Qt.UserRole + 3


def dot_icon(color: str, size=12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


def _no_where(sql: str) -> bool:
    """UPDATE/DELETE sem WHERE (ignora conteúdo de strings)."""
    w = leading_words(sql, 1)
    if not w or w[0] not in ("UPDATE", "DELETE"):
        return False
    clean = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return re.search(r"\bWHERE\b", clean, re.I) is None


# ===========================================================================
class HistoryPanel(QWidget):
    """Aba "Histórico" do resultado: SQLs já executados neste banco."""

    COLS = ["Quando", "", "Tempo", "Resultado", "SQL"]

    def __init__(self, tab: "SqlTab"):
        super().__init__()
        self.tab = tab
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar no histórico deste banco…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.only_errors = QCheckBox("Só erros")
        self.only_errors.toggled.connect(lambda _: self.refresh())
        self.count = QLabel()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.only_errors)
        bar.addWidget(self.count)
        lay.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        for i, mode in enumerate((QHeaderView.ResizeToContents,) * 4 + (QHeaderView.Stretch,)):
            hh.setSectionResizeMode(i, mode)
        self.table.cellDoubleClicked.connect(lambda r, _: self._insert(r))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.table)

    def refresh(self):
        p = self.tab.profile
        items = self.tab.main.history.for_profile(self.tab.profile_id, p.name if p else "")
        q = self.search.text().strip().lower()
        if q:
            items = [h for h in items if q in h["sql"].lower() or q in (h.get("info") or "").lower()]
        if self.only_errors.isChecked():
            items = [h for h in items if not h["ok"]]
        items = items[:500]
        self.table.setRowCount(len(items))
        red = QColor(theme.c("red"))
        for r, h in enumerate(items):
            one_line = " ".join(h["sql"].split())
            cells = [h["ts"], "✓" if h["ok"] else "✗", f"{h.get('elapsed', 0) * 1000:.0f} ms",
                     h.get("info") or "", one_line[:300]]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c == 4:
                    it.setToolTip(h["sql"][:3000])
                    it.setData(Qt.UserRole, h["sql"])
                if not h["ok"]:
                    it.setForeground(QBrush(red))
                self.table.setItem(r, c, it)
        self.count.setText(f"{len(items)} execução(ões)")

    def _sql(self, row):
        it = self.table.item(row, 4)
        return it.data(Qt.UserRole) if it else None

    def _insert(self, row, run=False):
        sql = self._sql(row)
        if sql:
            self.tab.append_sql(kind_of(sql), sql, run)

    def _menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        m = QMenu(self)
        m.addAction("Inserir no editor", lambda: self._insert(row))
        m.addAction("Inserir e executar", lambda: self._insert(row, run=True))
        m.addAction("Copiar SQL", lambda: QApplication.clipboard().setText(self._sql(row) or ""))
        m.exec(self.table.viewport().mapToGlobal(pos))


class SqlTab(QWidget):
    state_changed = Signal()
    executed = Signal(object, str, str, bool, float, str)   # id do banco, nome, sql, ok, tempo, resultado

    def __init__(self, main: "MainWindow", profile_id: str | None):
        super().__init__()
        self.main = main
        self.profile_id = None
        self.session: Session | None = None
        self._loading = False            # carregando workspace: não dispara o salvamento automático
        self.autocommit = False
        self.running = False
        self.result_views: list[ResultView] = []
        self.last_view: ResultView | None = None
        self.last_status = ""
        self.connecting = False
        self.fetching = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # cabeçalho com o banco da aba
        head = QWidget()
        head.setObjectName("tabhead")   # o estilo da faixa não deve vazar para os filhos
        head.setAttribute(Qt.WA_StyledBackground, True)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(6, 3, 6, 3)
        self.banner = QLabel()
        self.banner.setStyleSheet("font-weight:bold;")
        self.conn_combo = QComboBox()
        self.conn_combo.setMinimumWidth(280)
        self.conn_combo.activated.connect(self._combo_changed)
        self.tx_label = QLabel()
        hl.addWidget(QLabel("Banco:"))
        hl.addWidget(self.conn_combo)
        hl.addWidget(self.banner, 1)
        hl.addWidget(self.tx_label)
        self.head = head
        lay.addWidget(head)

        split = QSplitter(Qt.Vertical)
        # uma sub-aba por tipo de comando; o conteúdo de cada uma fica salvo no banco da aba
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setDocumentMode(True)
        self.editors: dict[str, SqlEditor] = {}
        for kind in SUB_TABS:
            ed = SqlEditor()
            ed.textChanged.connect(ed.clear_error)
            ed.textChanged.connect(self._schedule_save)
            self.editors[kind] = ed
            self.sub_tabs.addTab(ed, theme.icon(SUB_ICONS[kind][0], SUB_ICONS[kind][1]), kind)
            self.sub_tabs.setTabToolTip(self.sub_tabs.count() - 1, SUB_TIPS[kind])
        self.sub_tabs.currentChanged.connect(lambda _: (self._schedule_save(), self.state_changed.emit()))
        self._exec_editor: SqlEditor | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save_workspace)
        split.addWidget(self.sub_tabs)

        self.out_tabs = QTabWidget()
        self.out_tabs.setDocumentMode(True)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.out_tabs.addTab(self.messages, "Mensagens")
        self.history_panel = HistoryPanel(self)
        self.out_tabs.addTab(self.history_panel, theme.icon("history", "icon"), "Histórico")
        # resultado à esquerda, gráfico de tempos das execuções à direita
        out_split = QSplitter(Qt.Horizontal)
        out_split.addWidget(self.out_tabs)
        self.perf_chart = PerfChart()
        out_split.addWidget(self.perf_chart)
        out_split.setStretchFactor(0, 3)
        out_split.setStretchFactor(1, 1)
        out_split.setSizes([750, 280])
        split.addWidget(out_split)
        self.out_split = out_split
        out_split.setVisible(getattr(main, "bottom_visible", True))
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([400, 300])
        lay.addWidget(split, 1)

        self.refresh_connections()
        self.set_profile(profile_id, ask=False)

    # ---------------------------------------------------------------
    @property
    def profile(self):
        return self.main.store.get(self.profile_id) if self.profile_id else None

    @property
    def editor(self) -> SqlEditor:
        """Editor da sub-aba visível."""
        return self.sub_tabs.currentWidget()

    @property
    def kind(self) -> str:
        return SUB_TABS[self.sub_tabs.currentIndex()]

    def title(self) -> str:
        p = self.profile
        return p.name if p else "Sem banco"

    # --- workspace (SQL salvo por banco) ------------------------------
    def _schedule_save(self):
        if not self._loading:
            self._save_timer.start()

    def save_workspace(self):
        self._save_timer.stop()
        save_workspace(self.profile_id, {
            "sql": {k: ed.toPlainText() for k, ed in self.editors.items()},
            "current": self.kind,
            "cursor": {k: ed.textCursor().position() for k, ed in self.editors.items()},
        })

    def load_workspace(self):
        data = load_workspace(self.profile_id)
        self._loading = True
        try:
            for k, ed in self.editors.items():
                ed.setPlainText(data["sql"].get(k, ""))
                c = ed.textCursor()
                c.setPosition(min(int(data["cursor"].get(k, 0) or 0), len(ed.toPlainText())))
                ed.setTextCursor(c)
                ed.document().clearUndoRedoStacks()
            self.sub_tabs.setCurrentIndex(SUB_TABS.index(data["current"]))
        finally:
            self._loading = False

    def append_sql(self, kind: str, sql: str, run=False):
        """Acrescenta SQL no fim da sub-aba (nunca apaga o que já estava lá)."""
        self.sub_tabs.setCurrentIndex(SUB_TABS.index(kind))
        ed = self.editors[kind]
        text = ed.toPlainText()
        body = text.rstrip()
        prefix = ""
        if body:
            last_line = body.splitlines()[-1].strip()
            # sem ";" o comando anterior e o novo virariam um só
            if not body.endswith((";", "^")) and not last_line.startswith("--"):
                prefix = ";"
            prefix += "\n\n"
        new = sql.strip()
        if not new.endswith((";", "^")):
            new += ";"
        c = ed.textCursor()
        c.beginEditBlock()
        c.setPosition(len(body))
        c.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)   # troca só os espaços finais
        c.insertText(prefix + new + "\n")
        c.endEditBlock()
        start = len(body) + len(prefix)
        c.setPosition(start)
        c.setPosition(start + len(new), QTextCursor.KeepAnchor)
        ed.setTextCursor(c)                    # trecho novo fica selecionado
        ed.centerCursor()
        ed.setFocus()
        if run:                                # executa exatamente a seleção (o SQL acrescentado)
            QTimer.singleShot(0, lambda: self.execute(False))

    def set_objects(self, objects):
        for ed in self.editors.values():
            ed.set_objects(objects)

    def set_columns(self, table, cols):
        for ed in self.editors.values():
            ed.set_columns(table, cols)

    def refresh_connections(self):
        self.conn_combo.clear()
        self.conn_combo.addItem("(selecione um banco)", None)
        for env in ENVIRONMENTS:
            for p in sorted((p for p in self.main.store.profiles if p.environment == env), key=lambda p: p.name.lower()):
                self.conn_combo.addItem(dot_icon(ENV_COLORS[env]), f"[{env}] {p.name}", p.id)
        i = self.conn_combo.findData(self.profile_id)
        self.conn_combo.setCurrentIndex(max(0, i))

    def _combo_changed(self, _):
        pid = self.conn_combo.currentData()
        if pid != self.profile_id and not self.set_profile(pid):
            self.conn_combo.setCurrentIndex(max(0, self.conn_combo.findData(self.profile_id)))

    def set_profile(self, pid, ask=True) -> bool:
        other = self.main.tab_for(pid)
        if other is not None and other is not self:
            # um banco = uma aba (as duas editariam o mesmo SQL salvo)
            self.main.tabs.setCurrentWidget(other)
            return False
        if ask and not self.close_session():
            return False
        elif not ask and self.session:
            self.close_session(force=True)
        first_load = not hasattr(self, "_loaded_once")
        if not first_load:
            self.save_workspace()                      # guarda o SQL do banco anterior
        carry = None
        if not first_load and self.profile_id is None and pid and workspace_is_empty(pid):
            # escreveu SQL antes de escolher o banco: leva o texto junto
            carry = {k: ed.toPlainText() for k, ed in self.editors.items()}
        self._loaded_once = True
        self.profile_id = pid
        self.load_workspace()
        if carry and any(t.strip() for t in carry.values()):
            self._loading = True
            for k, t in carry.items():
                self.editors[k].setPlainText(t)
            self._loading = False
            self.save_workspace()
        p = self.profile
        self.autocommit = bool(p and p.autocommit and not p.is_client)
        i = self.conn_combo.findData(pid)
        self.conn_combo.setCurrentIndex(max(0, i))
        if p:
            color = ENV_COLORS.get(p.environment, "#555")
            prefix = "⛔ CLIENTE  " if p.is_client else "⚠ PRODUÇÃO  " if p.is_production else ""
            self.banner.setText(prefix + (f"{p.environment.upper()} — " if not p.is_critical else "— ")
                                + f"{p.host + ':' if p.host else ''}{p.database}"
                                + ("  [somente leitura]" if p.read_only else ""))
            if p.is_client:   # faixa sólida: impossível não perceber
                self.banner.setStyleSheet(f"background:{color}; color:white; font-weight:bold; padding:2px 8px;"
                                          "border-radius:3px;")
                self.head.setStyleSheet(f"#tabhead {{ background:{_rgba(color, 0.18)}; border-bottom:4px solid {color}; }}")
            else:
                self.banner.setStyleSheet(f"color:{color}; font-weight:bold;")
                self.head.setStyleSheet(f"#tabhead {{ background:{_rgba(color, 0.12)}; border-bottom:3px solid {color}; }}"
                                        if p.is_production else f"#tabhead {{ border-bottom:3px solid {color}; }}")
            for ed in self.editors.values():
                ed.clear_completion_cache()
                ed.columns_provider = lambda table, pid=pid: self.main.request_columns(pid, table)
                ed.structure_handler = lambda name, pid=pid: self.main.show_structure(pid, name)
            self.main.ensure_objects(pid)
            self.set_objects(self.main.meta_objects.get(pid) or {})
            for t, cols in self.main.columns_cache.get(pid, {}).items():
                self.set_columns(t, cols)
        else:
            self.banner.setText("")
            self.head.setStyleSheet("")
            for ed in self.editors.values():
                ed.structure_handler = None
        self.history_panel.refresh()
        self._update_tx()
        self.state_changed.emit()
        if p:
            # conecta já ao escolher o banco: o primeiro SQL não paga o custo da conexão
            QTimer.singleShot(0, self.connect_now)
        return True

    def connect_now(self):
        if self.profile is None or (self.session and self.session.is_open):
            return
        s = self.ensure_session()
        if s is not None:
            self.connecting = True
            self._update_tx()
            s.open()

    # ---------------------------------------------------------------
    def ensure_session(self) -> Session | None:
        p = self.profile
        if p is None:
            QMessageBox.information(self, "SQL", "Escolha o banco desta aba antes de executar.")
            return None
        if self.session is None:
            pw = self.main.password_for(p)
            if pw is None:
                return None
            s = Session(p, pw, self)
            s.statement_done.connect(self._on_statement)
            s.script_done.connect(self._on_script_done)
            s.more_rows.connect(self._on_more_rows)
            s.fetch_finished.connect(self._on_fetch_finished)
            s.tx_state.connect(lambda _: self._update_tx())
            s.failed.connect(self._on_failed)
            s.call_done.connect(self._on_call_done)
            s.connected.connect(lambda v: self._on_connected(p.name, v))
            self.session = s
        return self.session

    def close_session(self, force=False) -> bool:
        """Fecha a conexão da aba. Pergunta o que fazer com a transação pendente."""
        s = self.session
        if s is None:
            return True
        if s.busy and not force:
            QMessageBox.information(self, "SQL", "Aguarde a execução terminar (ou clique em Parar).")
            return False
        if s.dirty and not force:
            r = QMessageBox.question(
                self, "Transação pendente",
                f"A aba em '{self.profile.name if self.profile else '?'}' tem alterações sem commit.\n\n"
                "Deseja fazer COMMIT antes de fechar?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return False
            if r == QMessageBox.Yes:
                s.worker.commit()  # thread ociosa: seguro chamar direto
                if s.dirty:
                    return False
        self.session = None
        self.connecting = False
        s.close()
        s.deleteLater()
        self._update_tx()
        return True

    # ---------------------------------------------------------------
    def log(self, text, error=False):
        if error:
            self.messages.appendHtml(f'<span style="color:{theme.c("red")}">{_html(text)}</span>')
        else:
            self.messages.appendPlainText(text)

    def _on_connected(self, name, version):
        self.connecting = False
        self.log(f"Conectado em {name}" + (f" (servidor {version})" if version else ""))
        self._update_tx()

    def _update_tx(self):
        if self.session and self.session.dirty:
            self.tx_label.setText("  ● TRANSAÇÃO PENDENTE — faça Commit ou Rollback  ")
            self.tx_label.setStyleSheet("background:#ff9800; color:black; font-weight:bold; border-radius:3px;")
        elif self.session and self.connecting:
            self.tx_label.setText("conectando…")
            self.tx_label.setStyleSheet(f"color:{theme.c('orange')};")
        elif self.session and self.session.is_open:
            self.tx_label.setText("conectado" + (" · auto-commit" if self.autocommit else ""))
            self.tx_label.setStyleSheet(f"color:{theme.c('green')};")
        else:
            self.tx_label.setText("desconectado")
            self.tx_label.setStyleSheet(f"color:{theme.c('muted')};")
        self.state_changed.emit()

    def statements(self, whole_script: bool):
        drv = get_driver(self.profile.driver) if self.profile else get_driver("firebird")
        c = self.editor.textCursor()
        text = self.editor.toPlainText()
        if c.hasSelection() and not whole_script:
            a, b = c.selectionStart(), c.selectionEnd()
            sts = split_sql(text[a:b], drv.key, drv.block_prefixes)
            for s in sts:
                s.start += a
                s.end += a
            return sts
        sts = split_sql(text, drv.key, drv.block_prefixes)
        if whole_script:
            return sts
        pos = c.position()
        for s in sts:            # comando sob o cursor
            if s.start <= pos <= s.end + 1:
                return [s]
        prev = [s for s in sts if s.end < pos]
        return [prev[-1]] if prev else sts[:1]

    def execute(self, whole_script=False):
        if self.running:
            return
        p = self.profile
        if p is None:
            QMessageBox.information(self, "SQL", "Escolha o banco desta aba antes de executar.")
            return
        sts = self.statements(whole_script)
        if not sts:
            return
        writes = [s for s in sts if not is_read_only(s.sql)]
        if writes and p.read_only:
            QMessageBox.warning(self, "Somente leitura",
                                f"A conexão '{p.name}' está marcada como somente leitura.\n"
                                "Comandos de alteração não são permitidos.")
            return
        if self.kind != "OUTROS":
            # SELECT pode rodar em qualquer sub-aba; alteração na sub-aba de outro tipo pede confirmação
            wrong = [s for s in sts if kind_of(s.sql) not in (self.kind, "SELECT")]
            if wrong:
                k = kind_of(wrong[0].sql)
                r = QMessageBox.question(
                    self, "Sub-aba errada?",
                    f"Este comando é um {k}, mas está na sub-aba {self.kind}:\n\n{_preview(wrong[0].sql)}\n\n"
                    f"Executar mesmo assim? (o lugar dele é a sub-aba {k})",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if r != QMessageBox.Yes:
                    return
        no_where = [s for s in sts if _no_where(s.sql)]
        if no_where:
            r = QMessageBox.warning(self, "UPDATE/DELETE sem WHERE",
                                    "Este comando vai afetar TODAS as linhas da tabela:\n\n"
                                    + _preview(no_where[0].sql) + "\n\nContinuar?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        if writes and p.is_client:
            if not confirm_typed(self, "⛔ CLIENTE",
                                 f"Você vai executar {len(writes)} comando(s) de ALTERAÇÃO no banco do CLIENTE "
                                 f"'{p.name}'.\n\n{_preview(writes[0].sql)}\n\n"
                                 f"Para confirmar, digite o nome da conexão:", p.name):
                return
        elif writes and p.is_production:
            box = QMessageBox(QMessageBox.Warning, "⚠ PRODUÇÃO",
                              f"Você vai executar {len(writes)} comando(s) de ALTERAÇÃO no banco de "
                              f"PRODUÇÃO '{p.name}'.\n\n" + _preview(writes[0].sql)
                              + ("\n\n(auto-commit LIGADO: será gravado imediatamente)" if self.autocommit else ""),
                              QMessageBox.Yes | QMessageBox.Cancel, self)
            box.button(QMessageBox.Yes).setText("Executar em PRODUÇÃO")
            box.setDefaultButton(QMessageBox.Cancel)
            if box.exec() != QMessageBox.Yes:
                return
        s = self.ensure_session()
        if s is None:
            return
        self._exec_editor = self.editor
        self.editor.clear_error()
        self._clear_results()
        self.running = True
        self._script_sts = sts
        self.log(f"--- {len(sts)} comando(s) em {p.name} ---" if len(sts) > 1 else f"--- {p.name} ---")
        self.state_changed.emit()
        s.run([dict(sql=x.sql, start=x.start, end=x.end) for x in sts], self.autocommit,
              self.main.max_rows.value(), True)

    def _clear_results(self):
        for v in self.result_views:
            i = self.out_tabs.indexOf(v)
            if i >= 0:
                self.out_tabs.removeTab(i)
            v.deleteLater()
        self.result_views.clear()
        self.last_view = None

    def _on_statement(self, info):
        p = self.profile
        name = p.name if p else "?"
        if info["error"]:
            summary = "ERRO: " + _error_summary(info["error"])
        elif info["columns"] is not None:
            summary = f"{len(info['rows'])}{'+' if info['has_more'] else ''} linha(s)"
        else:
            rc = info["rowcount"]
            summary = f"{rc} afetada(s)" if rc is not None and rc >= 0 else "OK"
        self.executed.emit(self.profile_id, name, info["sql"], info["error"] is None, info["elapsed"], summary)
        if info["columns"] is not None:   # SELECT: execução + busca da primeira página
            exec_t, fetch_t = info.get("exec_time", info["elapsed"]), info.get("fetch_time", 0.0)
        else:                             # DML/DDL: não há busca, o tempo todo conta como execução
            exec_t, fetch_t = info["elapsed"], 0.0
        self.perf_chart.add(info["sql"], exec_t, fetch_t, info["error"] is None, summary)
        ms = f"{info['elapsed'] * 1000:.0f} ms"
        if info["error"]:
            self.log(f"ERRO ({ms}):\n{info['error']}\n» {_preview(info['sql'])}", error=True)
            (self._exec_editor or self.editor).mark_error(info["start"], info["end"])
            self.out_tabs.setCurrentWidget(self.messages)
            self.last_status = "Erro na execução"
            return
        if self.last_view is not None:
            self.last_view.set_has_more(False)   # novo comando fechou o cursor do resultado anterior
        if info["columns"] is not None:
            if len(self.result_views) >= MAX_RESULT_TABS:
                self.log(f"(resultado {info['index'] + 1} omitido — limite de {MAX_RESULT_TABS} abas)")
                return
            view = ResultView(info["columns"], info["rows"], info["has_more"], _guess_table(info["sql"]))
            view.fetch_more_requested.connect(self._fetch_more)
            self.result_views.append(view)
            self.last_view = view
            n = len(self.result_views)
            self.out_tabs.insertTab(n - 1, view, f"Resultado {n}" if len(self._script_sts) > 1 else "Resultado")
            self.out_tabs.setCurrentWidget(view)
            more = "+" if info["has_more"] else ""
            self.log(f"{len(info['rows'])}{more} linha(s) em {ms}")
            self.last_status = f"{len(info['rows'])}{more} linha(s) · {ms}"
        else:
            rc = info["rowcount"]
            verb = leading_words(info["sql"], 1)
            msg = (f"{rc} linha(s) afetada(s)" if rc is not None and rc >= 0 else "OK") + f" — {verb[0] if verb else ''} em {ms}"
            self.log(msg)
            self.last_status = msg
            if not self.result_views:
                self.out_tabs.setCurrentWidget(self.messages)

    def _on_script_done(self, summary):
        self.running = False
        if summary.get("cancelled"):
            self.log("Execução interrompida pelo usuário.", error=True)
        if summary["total"] > 1:
            self.log(f"Script: {summary['ok']} ok, {summary['errors']} erro(s) em {summary.get('elapsed', 0):.2f}s")
        if self.autocommit and summary["ok"]:
            self.log("(auto-commit)")
        if any(not is_read_only(x.sql) for x in self._script_sts) and not self.autocommit and summary["ok"]:
            self.log("Lembre-se: as alterações só serão gravadas após o COMMIT.")
        self.main.on_script_finished(self, self._script_sts)
        self._update_tx()

    def _fetch_more(self):
        """A rolagem do resultado chegou ao fim: busca a próxima página em segundo plano."""
        view = self.sender()
        if self.session is None or view is not self.last_view:
            view.set_has_more(False)       # cursor desse resultado já foi fechado
            return
        if self.running:
            view.set_has_more(True)        # ocupado (script rodando): tenta de novo na próxima rolagem
            return
        # enquanto busca, a aba conta como "executando": Parar fica ativo
        self.running = self.fetching = True
        self.state_changed.emit()
        self.session.fetch_more(self.main.max_rows.value())

    def _on_more_rows(self, rows, has_more):
        if self.last_view:
            self.last_view.append_rows(rows, has_more)
            self.last_status = f"{len(self.last_view.model.rows)} linha(s)"
            self.state_changed.emit()

    def _on_fetch_finished(self, note):
        if note:
            self.log(note, error=True)
            self.last_status = note
        if self.fetching:
            self.running = self.fetching = False
        self._update_tx()

    def _on_failed(self, ctx, msg):
        self.fetching = False
        self.running = False
        if ctx == "connect":
            self.connecting = False
            self.log(f"Falha ao conectar:\n{msg}", error=True)
            self.out_tabs.setCurrentWidget(self.messages)
            s = self.session
            self.session = None
            if s:
                s.close()
                s.deleteLater()
        else:
            self.log(f"Erro em {ctx}:\n{msg}", error=True)
            self.out_tabs.setCurrentWidget(self.messages)
        self._update_tx()

    def _on_call_done(self, tag, _):
        if tag == "commit":
            self.log("COMMIT efetuado.")
            self.last_status = "Commit efetuado"
        elif tag == "rollback":
            self.log("ROLLBACK efetuado.")
            self.last_status = "Rollback efetuado"
        if self.last_view:
            self.last_view.set_has_more(False)
        self._update_tx()

    def format_sql(self):
        """Formata o SQL selecionado (sem seleção: o editor inteiro). Desfaz com Ctrl+Z."""
        drv = get_driver(self.profile.driver) if self.profile else get_driver("firebird")
        c = self.editor.textCursor()
        if not c.hasSelection():
            c.select(QTextCursor.Document)
        text = c.selection().toPlainText()
        if not text.strip():
            return
        try:
            new, formatted, kept = format_sql(text, drv.key, drv.block_prefixes)
        except Exception as e:
            QMessageBox.warning(self, "Formatar SQL", f"Não foi possível formatar:\n{e}")
            return
        start = c.selectionStart()
        if new != text:
            c.beginEditBlock()
            c.insertText(new)
            c.endEditBlock()
            c.setPosition(start)
            c.setPosition(start + len(new), QTextCursor.KeepAnchor)
            self.editor.setTextCursor(c)
        self.last_status = f"{formatted} comando(s) formatado(s)" + (
            f", {kept} bloco(s) PSQL mantido(s)" if kept else "")
        self.state_changed.emit()

    def commit(self):
        if self.session and not self.running:
            p = self.profile
            if p and p.is_client and self.session.dirty:
                if not confirm_typed(self, "⛔ CLIENTE", f"COMMIT no banco do CLIENTE '{p.name}' grava as alterações "
                                     "definitivamente.\n\nPara confirmar, digite o nome da conexão:", p.name):
                    return
            elif p and p.is_production and self.session.dirty:
                if QMessageBox.question(self, "⚠ PRODUÇÃO", f"Confirmar COMMIT em PRODUÇÃO ({p.name})?") != QMessageBox.Yes:
                    return
            self.session.commit()

    def rollback(self):
        if self.session and not self.running:
            self.session.rollback()

    def stop(self):
        if self.session and self.running:
            self.session.cancel()
            self.log("Cancelamento solicitado…")

    def disconnect(self):
        self.close_session()

    def reconnect(self):
        """Derruba a conexão da aba e abre outra (conexão caída, servidor reiniciado, metadados novos)."""
        p = self.profile
        if p is None:
            QMessageBox.information(self, "Reconectar", "Escolha o banco desta aba primeiro.")
            return
        s = self.session
        if s is not None and s.busy:
            QMessageBox.information(self, "Reconectar", "Aguarde a execução terminar (ou clique em Parar).")
            return
        if s is not None and s.dirty:
            if QMessageBox.warning(
                    self, "Transação pendente",
                    f"A aba em '{p.name}' tem alterações sem commit.\n\n"
                    "Reconectar faz ROLLBACK e descarta essas alterações. Continuar?",
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                return
        self.close_session(force=True)      # fecha com rollback
        self.log(f"Reconectando em {p.name}…")
        self.connect_now()

    def export_file(self) -> bool:
        """Salva a sub-aba atual como arquivo .sql."""
        name = f"{self.profile.name if self.profile else 'sql'}-{self.kind.lower()}.sql"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar SQL", name, "SQL (*.sql);;Todos (*)")
        if not path:
            return False
        Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
        self.last_status = f"Exportado: {Path(path).name}"
        self.state_changed.emit()
        return True


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {int(alpha * 255)})"


def confirm_typed(parent, title, message, expected) -> bool:
    """Confirmação forte: o usuário precisa digitar `expected` (sem diferenciar maiúsculas)."""
    text, ok = QInputDialog.getText(parent, title, message)
    if not ok:
        return False
    if text.strip().lower() != expected.strip().lower():
        QMessageBox.warning(parent, title, "Nome não confere. Nada foi executado.")
        return False
    return True


def _error_summary(err: str) -> str:
    """Resumo de uma linha: pula o cabeçalho genérico do Firebird e junta o que importa."""
    lines = [l.strip().lstrip("-").strip() for l in err.strip().splitlines()]
    noise = ("dynamic sql error", "sql error code", "at line", "at block line")
    useful = [l for l in lines if l and not l.lower().startswith(noise)]
    return " · ".join(useful or lines[:1])[:150]


def _html(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def _preview(sql, n=400):
    s = sql.strip()
    return s if len(s) <= n else s[:n] + " …"


def _guess_table(sql):
    m = re.search(r"\bFROM\s+([\w$.\"`\[\]]+)", sql, re.I)
    return m.group(1) if m else "TABELA"


# ===========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"SQL Ath {__version__}")
        self.resize(1400, 860)
        self.store = ConnectionStore()
        self.history = History()
        self.settings = QSettings("sqlath", "sqlath")
        self.meta_sessions: dict[str, Session] = {}
        self.meta_objects: dict[str, dict] = {}
        self.passwords: dict[str, str] = {}
        self.columns_cache: dict[str, dict[str, list[str]]] = {}
        self._loading_objects: set[str] = set()

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(lambda _: self.update_state())
        self.tabs.setDocumentMode(True)
        # sem abas abertas: mostra uma dica no lugar
        self.welcome = QLabel("Dê duplo clique em um banco na barra lateral para abrir.\n\n"
                              "Os SQLs de cada banco (SELECT, INSERT, UPDATE, DELETE e OUTROS)\n"
                              "ficam salvos e voltam quando você abrir o banco de novo.")
        self.welcome.setAlignment(Qt.AlignCenter)
        self.welcome.setStyleSheet("font-size: 13pt;")
        self.welcome.setEnabled(False)
        self.central = QStackedWidget()
        self.central.addWidget(self.welcome)
        self.central.addWidget(self.tabs)
        self.setCentralWidget(self.central)

        self._build_tree()
        self._build_history()
        self._build_actions()
        self.status_conn = QLabel()
        self.status_info = QLabel()
        self.statusBar().addWidget(self.status_conn, 1)
        self.statusBar().addPermanentWidget(self.status_info)

        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        st = self.settings.value("state")
        if st:
            self.restoreState(st)
        self.restore_tabs()
        if self.tabs.count() == 0:
            self.central.setCurrentWidget(self.welcome)
        self.update_state()

    # ------------------------------------------------------------ UI
    def _build_tree(self):
        dock = QDockWidget("Bancos de dados", self)
        dock.setObjectName("dock_tree")
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        self.tree_filter = QLineEdit()
        self.tree_filter.setPlaceholderText("Filtrar bancos / objetos…")
        self.tree_filter.setClearButtonEnabled(True)
        self.tree_filter.textChanged.connect(self._filter_tree)
        lay.addWidget(self.tree_filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemDoubleClicked.connect(self._tree_double_click)
        self.tree.itemExpanded.connect(self._tree_expanded)
        lay.addWidget(self.tree)
        dock.setWidget(w)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.dock_tree = dock
        self.populate_tree()

    def _build_history(self):
        dock = QDockWidget("Histórico de SQL", self)
        dock.setObjectName("dock_history")
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        self.hist_filter = QLineEdit()
        self.hist_filter.setPlaceholderText("Buscar no histórico…")
        self.hist_filter.setClearButtonEnabled(True)
        self.hist_filter.textChanged.connect(self._fill_history)
        self.hist_list = QListWidget()
        self.hist_list.itemDoubleClicked.connect(self._history_open)
        lay.addWidget(self.hist_filter)
        lay.addWidget(self.hist_list)
        dock.setWidget(w)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        dock.hide()
        self.dock_history = dock
        self._fill_history()

    def _act(self, text, slot, shortcut=None, icon=None, tip=None, checkable=False):
        """icon = (nome_tabler, chave_de_cor) — reaplicado quando o tema muda."""
        a = QAction(text, self)
        if icon is not None:
            self._icon_specs.append((a, icon))
            a.setIcon(theme.icon(*icon))
        if shortcut:
            a.setShortcuts([QKeySequence(s) for s in (shortcut if isinstance(shortcut, list) else [shortcut])])
        a.setToolTip((tip or text) + (f"  ({a.shortcut().toString()})" if shortcut else ""))
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self.addAction(a)
        return a

    def toggle_bottom(self):
        """Ctrl+J: recolhe/mostra o painel de baixo (resultado + gráfico) em todas as abas."""
        self.bottom_visible = not getattr(self, "bottom_visible", True)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, SqlTab):
                if not self.bottom_visible and tab.out_split.isAncestorOf(QApplication.focusWidget()):
                    tab.editor.setFocus()      # o foco não pode ficar num painel escondido
                tab.out_split.setVisible(self.bottom_visible)
        self.a_bottom.setChecked(self.bottom_visible)

    def _build_actions(self):
        self._icon_specs = []
        self.bottom_visible = True
        self.a_new = self._act("Nova aba SQL", lambda: self.new_tab(self._selected_pid()), "Ctrl+T", ("file-plus", "icon"))
        self.a_open = self._act("Abrir arquivo .sql…", self.open_file, "Ctrl+O", ("folder-open", "icon"),
                                "Importa um arquivo .sql para a sub-aba atual")
        self.a_save = self._act("Salvar", self.save_now, "Ctrl+S", ("device-floppy", "icon"),
                                "Os SQLs já salvam sozinhos; isto força o salvamento agora")
        self.a_save_as = self._act("Exportar sub-aba como .sql…", lambda: self.cur and self.cur.export_file(),
                                   "Ctrl+Shift+S")
        self.a_run = self._act("Executar", lambda: self.cur and self.cur.execute(False), ["F9", "Ctrl+Return"],
                               ("player-play", "green"), "Executa a seleção ou o comando sob o cursor")
        self.a_script = self._act("Executar script", lambda: self.cur and self.cur.execute(True),
                                  ["Ctrl+F9", "Ctrl+Shift+Return"], ("player-track-next", "green"),
                                  "Executa todos os comandos do editor")
        self.a_stop = self._act("Parar", lambda: self.cur and self.cur.stop(), "Ctrl+Pause", ("player-stop", "red"))
        self.a_commit = self._act("Commit", lambda: self.cur and self.cur.commit(), "Ctrl+Alt+C",
                                  ("circle-check", "green"), "Grava a transação")
        self.a_rollback = self._act("Rollback", lambda: self.cur and self.cur.rollback(), "Ctrl+Alt+R",
                                    ("arrow-back-up", "red"), "Desfaz a transação")
        self.a_auto = self._act("Auto-commit", self._toggle_autocommit, None, ("refresh", "orange"),
                                "Commit automático após cada comando", checkable=True)
        self.a_reconnect = self._act("Reconectar", lambda: self.cur and self.cur.reconnect(), "Ctrl+Shift+R",
                                     ("plug-connected", "blue"), "Fecha e abre de novo a conexão da aba com o banco")
        self.a_disconnect = self._act("Desconectar aba", lambda: self.cur and self.cur.disconnect(), None,
                                      ("plug-connected-x", "red"))
        self.a_find = self._act("Localizar…", self.find_text, "Ctrl+F", ("search", "icon"))
        self.a_find_next = self._act("Localizar próximo", self.find_next, "F3")
        self.a_format = self._act("Formatar SQL", lambda: self.cur and self.cur.format_sql(), "Ctrl+Shift+F",
                                  ("align-left", "icon"), "Formata o SQL selecionado (sem seleção: o editor inteiro)")
        self.a_comment = self._act("Comentar/descomentar", lambda: self.cur and self.cur.editor.toggle_comment(), None)
        self.a_new_conn = self._act("Nova conexão…", self.new_connection, "Ctrl+Shift+N", ("database-plus", "blue"))
        self.a_import = self._act("Importar db.json (apix)…", self.import_dbjson)
        self.a_import_cratos = self._act("Importar lista do Cratos (HTML)…", self.import_cratos)
        self.a_export_conns = self._act("Exportar conexões (sem senhas)…", self.export_connections,
                                        None, ("file-export", "icon"))

        tb = QToolBar("Principal")
        tb.setObjectName("tb_main")
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (self.a_new_conn, None, self.a_new, self.a_open, self.a_save, None, self.a_run, self.a_script,
                  self.a_stop, None, self.a_commit, self.a_rollback, self.a_auto, None, self.a_reconnect):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.tb_main = tb
        # ações de baixa prioridade aparecem só com ícone (texto fica no tooltip)
        for a in (self.a_new, self.a_open, self.a_save, self.a_stop):
            a.setPriority(QAction.LowPriority)
        tb.addWidget(QLabel(" Linhas por busca: "))
        self.max_rows = QSpinBox()
        self.max_rows.setRange(100, 100_000)
        self.max_rows.setSingleStep(500)
        self.max_rows.setValue(max(100, int(self.settings.value("max_rows", 1000) or 1000)))
        self.max_rows.setToolTip("Linhas buscadas por vez. O resto carrega sozinho ao rolar até o fim do resultado.")
        tb.addWidget(self.max_rows)
        self.addToolBar(tb)

        mb = self.menuBar()
        m = mb.addMenu("&Arquivo")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as):
            m.addAction(a)
        m.addSeparator()
        m.addAction("Sair", self.close)
        m = mb.addMenu("&Banco")
        for a in (self.a_new_conn, self.a_import, self.a_import_cratos, self.a_export_conns):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_reconnect)
        m.addAction(self.a_disconnect)
        m = mb.addMenu("&SQL")
        for a in (self.a_run, self.a_script, self.a_stop, None, self.a_commit, self.a_rollback, self.a_auto, None,
                  self.a_format, self.a_find, self.a_find_next, self.a_comment):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("E&xibir")
        a_tree = self.dock_tree.toggleViewAction()
        a_tree.setShortcut(QKeySequence("Ctrl+B"))
        self.addAction(a_tree)           # atalho funciona mesmo com o painel fechado
        m.addAction(a_tree)
        self.a_bottom = self._act("Painel de resultados", self.toggle_bottom, "Ctrl+J", checkable=True)
        self.a_bottom.setChecked(True)
        m.addAction(self.a_bottom)
        m.addAction(self.dock_history.toggleViewAction())
        m.addSeparator()
        self.a_reopen = m.addAction("Reabrir bancos abertos ao iniciar")
        self.a_reopen.setCheckable(True)
        self.a_reopen.setChecked(self.settings.value("reopen_tabs", False, type=bool))
        self.a_reopen.setToolTip("Desligado (padrão): o app abre sem abas e com a barra lateral recolhida")
        self.a_reopen.toggled.connect(lambda on: self.settings.setValue("reopen_tabs", on))
        m.addSeparator()
        tm = m.addMenu("Tema")
        grp = QActionGroup(self)
        self.a_theme = {}
        for key, label in (("light", "Claro"), ("dark", "Escuro")):
            a = tm.addAction(label, lambda k=key: self.set_theme(k))
            a.setCheckable(True)
            a.setChecked(theme.name() == key)
            grp.addAction(a)
            self.a_theme[key] = a
        m = mb.addMenu("Aj&uda")
        m.addAction("Atalhos", self.show_shortcuts)

    def set_theme(self, name):
        theme.apply(name)
        for a, spec in self._icon_specs:
            a.setIcon(theme.icon(*spec))
        self.a_theme[name].setChecked(True)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            for ed in tab.editors.values():
                ed.apply_theme()
            for i, k in enumerate(SUB_TABS):
                tab.sub_tabs.setTabIcon(i, theme.icon(*SUB_ICONS[k]))
            tab.out_tabs.setTabIcon(tab.out_tabs.indexOf(tab.history_panel), theme.icon("history", "icon"))
            tab.history_panel.refresh()
            tab._update_tx()
            for v in tab.result_views:
                v.view.viewport().update()
        self.populate_tree()
        self.update_state()

    @property
    def cur(self) -> SqlTab | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, SqlTab) else None

    def update_state(self):
        t = self.cur
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            self.tabs.setTabText(i, tab.title())
            p = tab.profile
            self.tabs.setTabIcon(i, dot_icon(ENV_COLORS.get(p.environment, "#999")) if p else QIcon())
            self.tabs.tabBar().setTabTextColor(i, QColor(ENV_COLORS[p.environment]) if p and p.is_critical
                                               else QApplication.palette().windowText().color())
        has = t is not None
        running = has and t.running
        dirty = has and t.session is not None and t.session.dirty
        open_ = has and t.session is not None
        self.a_run.setEnabled(has and not running)
        self.a_script.setEnabled(has and not running)
        self.a_stop.setEnabled(running)
        self.a_commit.setEnabled(open_ and not running)
        self.a_rollback.setEnabled(open_ and not running)
        self.a_disconnect.setEnabled(open_ and not running)
        self.a_reconnect.setEnabled(has and t.profile is not None and not running)
        self.a_auto.setEnabled(has and not (t.profile and t.profile.is_client))
        self.a_auto.setChecked(bool(has and t.autocommit))
        if has and t.profile:
            p = t.profile
            self.status_conn.setText(f"{p.environment} · {p.name} · {get_driver(p.driver).label}"
                                     + (" · TRANSAÇÃO PENDENTE" if dirty else ""))
            self.status_conn.setStyleSheet(f"color:{theme.c('orange')}; font-weight:bold;" if dirty else "")
        else:
            self.status_conn.setText("Nenhum banco selecionado")
            self.status_conn.setStyleSheet("")
        self.status_info.setText(("Executando…" if running else t.last_status) if has else "")
        self._refresh_tree_state()

    def _toggle_autocommit(self, on):
        t = self.cur
        if not t:
            return
        if on and t.profile and t.profile.is_client:
            QMessageBox.warning(self, "⛔ CLIENTE", "Auto-commit é bloqueado em bancos de CLIENTE.\n"
                                "Cada alteração precisa de Commit manual confirmado.")
            self.a_auto.setChecked(False)
            return
        if on and t.session and t.session.dirty:
            QMessageBox.information(self, "Auto-commit", "Há uma transação pendente. Faça Commit ou Rollback antes.")
            self.a_auto.setChecked(False)
            return
        t.autocommit = on
        t._update_tx()

    # ------------------------------------------------------------ abas
    def tab_for(self, profile_id) -> SqlTab | None:
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).profile_id == profile_id:
                return self.tabs.widget(i)
        return None

    def new_tab(self, profile_id=None, sql: str | None = None, kind: str | None = None, run=False) -> SqlTab:
        """Abre (ou traz para frente) a aba do banco. SQL informado é acrescentado na sub-aba certa."""
        tab = self.tab_for(profile_id)
        if tab is None:
            tab = SqlTab(self, profile_id)
            tab.state_changed.connect(self.update_state)
            tab.executed.connect(self._add_history)
            self.tabs.addTab(tab, tab.title())
        self.tabs.setCurrentWidget(tab)
        self.central.setCurrentWidget(self.tabs)
        if sql:
            tab.append_sql(kind or kind_of(sql), sql, run)
        else:
            tab.editor.setFocus()
        return tab

    def close_tab(self, index):
        tab = self.tabs.widget(index)
        if not tab.close_session():
            return
        tab.save_workspace()                 # o SQL continua salvo no banco
        self.tabs.removeTab(index)
        tab.deleteLater()
        if self.tabs.count() == 0:
            self.central.setCurrentWidget(self.welcome)
        self.update_state()

    def open_file(self):
        """Importa arquivos .sql para a sub-aba atual (abre uma aba 'Sem banco' se não houver nenhuma)."""
        paths, _ = QFileDialog.getOpenFileNames(self, "Abrir SQL", "", "SQL (*.sql);;Todos (*)")
        for p in paths:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
            tab = self.cur or self.new_tab(None)
            tab.append_sql(tab.kind, text)

    def save_now(self):
        if self.cur:
            self.cur.save_workspace()
            self.cur.last_status = "SQLs salvos"
            self.update_state()

    def find_text(self):
        t = self.cur
        if not t:
            return
        sel = t.editor.textCursor().selectedText()
        text, ok = QInputDialog.getText(self, "Localizar", "Texto:", text=sel or getattr(self, "_find", ""))
        if ok and text:
            self._find = text
            self.find_next()

    def find_next(self):
        t = self.cur
        text = getattr(self, "_find", "")
        if not t or not text:
            return
        if not t.editor.find(text):
            c = t.editor.textCursor()
            c.movePosition(c.MoveOperation.Start)
            t.editor.setTextCursor(c)
            if not t.editor.find(text):
                self.statusBar().showMessage(f"'{text}' não encontrado", 3000)

    # ------------------------------------------------------------ senhas
    def password_for(self, p) -> str | None:
        if p.id in self.passwords:
            return self.passwords[p.id]
        pw = self.store.get_password(p)
        if not pw and p.user:
            pw, ok = QInputDialog.getText(self, "Senha", f"Senha de {p.user} em {p.name}:", QLineEdit.Password)
            if not ok:
                return None
        self.passwords[p.id] = pw
        return pw

    # ------------------------------------------------------------ árvore
    def _selected_pid(self):
        it = self.tree.currentItem()
        return it.data(0, R_PID) if it else None

    def populate_tree(self):
        expanded = {self.tree.topLevelItem(i).text(0) for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        self.tree.clear()
        bold = QFont()
        bold.setBold(True)
        for env in ENVIRONMENTS:
            profiles = sorted((p for p in self.store.profiles if p.environment == env), key=lambda p: p.name.lower())
            if not profiles:
                continue
            g = QTreeWidgetItem([f"{env} ({len(profiles)})"])
            g.setData(0, R_KIND, "env")
            g.setFont(0, bold)
            g.setForeground(0, QBrush(QColor(ENV_COLORS[env])))
            g.setIcon(0, dot_icon(ENV_COLORS[env], 14))
            self.tree.addTopLevelItem(g)
            for p in profiles:
                it = QTreeWidgetItem([p.name])
                it.setData(0, R_KIND, "conn")
                it.setData(0, R_PID, p.id)
                it.setToolTip(0, f"{get_driver(p.driver).label}\n{p.host}{':' + p.port if p.port else ''}\n"
                                 f"{p.database}\nusuário: {p.user}" + (f"\n\n{p.notes}" if p.notes else ""))
                it.setIcon(0, theme.icon("database", "blue"))
                g.addChild(it)
                it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                if p.id in self.meta_objects:
                    self._fill_objects(it, self.meta_objects[p.id])
            g.setExpanded(g.text(0) in expanded)   # na abertura do app: tudo recolhido
        self._refresh_tree_state()
        self._filter_tree(self.tree_filter.text())

    def _conn_item(self, pid):
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                if g.child(j).data(0, R_PID) == pid:
                    return g.child(j)
        return None

    def _refresh_tree_state(self):
        open_pids = set(self.meta_sessions) | {self.tabs.widget(i).profile_id for i in range(self.tabs.count())
                                               if self.tabs.widget(i).session is not None}
        dirty_pids = {self.tabs.widget(i).profile_id for i in range(self.tabs.count())
                      if self.tabs.widget(i).session is not None and self.tabs.widget(i).session.dirty}
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                it = g.child(j)
                pid = it.data(0, R_PID)
                f = it.font(0)
                f.setBold(pid in open_pids)
                it.setFont(0, f)
                p = self.store.get(pid)
                it.setText(0, (p.name if p else "?") + ("  ● pendente" if pid in dirty_pids else ""))
                if pid in dirty_pids:
                    it.setForeground(0, QBrush(QColor(theme.c("orange"))))
                else:
                    it.setData(0, Qt.ForegroundRole, None)   # cor padrão do tema atual

    def _filter_tree(self, text):
        text = text.strip().lower()

        def visit(item) -> bool:
            match = not text or text in item.text(0).lower()
            child_match = False
            for k in range(item.childCount()):
                child_match = visit(item.child(k)) or child_match
            show = match or child_match
            item.setHidden(not show)
            if text and child_match:
                item.setExpanded(True)
            return show

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        m = QMenu(self)
        kind = it.data(0, R_KIND) if it else None
        pid = it.data(0, R_PID) if it else None
        if kind == "conn":
            m.addAction("Nova aba SQL", lambda: self.new_tab(pid))
            if pid in self.meta_sessions:
                m.addAction("Atualizar objetos", lambda: self.connect_meta(pid, refresh=True))
                m.addAction("Desconectar árvore", lambda: self.disconnect_meta(pid))
            else:
                m.addAction("Conectar (carregar objetos)", lambda: self.connect_meta(pid))
            m.addSeparator()
            m.addAction("Editar conexão…", lambda: self.edit_connection(pid))
            m.addAction("Duplicar conexão…", lambda: self.duplicate_connection(pid))
            m.addAction("Excluir conexão", lambda: self.delete_connection(pid))
        elif kind == "obj":
            cat, name = it.data(0, R_CAT), it.data(0, R_NAME)
            drv = get_driver(self.store.get(pid).driver)
            if cat in ("Tabelas", "Views"):
                m.addAction("SELECT primeiras linhas", lambda: self.new_tab(pid, drv.top_sql(name, 100), "SELECT", run=True))
                m.addAction("SELECT COUNT(*)",
                            lambda: self.new_tab(pid, f"SELECT COUNT(*) FROM {drv.qualified(name)}", "SELECT", run=True))
            m.addAction("Ver DDL / código-fonte", lambda: self.show_source(pid, cat, name))
            if cat in ("Tabelas", "Views"):
                m.addAction("Gerar SELECT com colunas", lambda: self.gen_sql(pid, name, "select"))
                m.addAction("Gerar INSERT", lambda: self.gen_sql(pid, name, "insert"))
                m.addAction("Gerar UPDATE", lambda: self.gen_sql(pid, name, "update"))
            m.addSeparator()
            m.addAction("Copiar nome", lambda: QApplication.clipboard().setText(name))
            m.addAction("Inserir nome no editor", lambda: self.cur and self.cur.editor.insertPlainText(name))
        elif kind == "col":
            name = it.data(0, R_NAME)
            m.addAction("Inserir nome no editor", lambda: self.cur and self.cur.editor.insertPlainText(name))
            m.addAction("Copiar nome", lambda: QApplication.clipboard().setText(name))
        else:
            m.addAction("Nova conexão…", self.new_connection)
            m.addAction("Importar db.json (apix)…", self.import_dbjson)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _tree_double_click(self, it, _col):
        kind = it.data(0, R_KIND)
        pid = it.data(0, R_PID)
        if kind == "conn":
            # abre aba SQL e já carrega os objetos
            self.new_tab(pid)
            if pid not in self.meta_sessions:
                self.connect_meta(pid)
        elif kind == "obj":
            cat, name = it.data(0, R_CAT), it.data(0, R_NAME)
            drv = get_driver(self.store.get(pid).driver)
            if cat in ("Tabelas", "Views"):
                self.new_tab(pid, drv.top_sql(name, 100), "SELECT", run=True)
            else:
                self.show_source(pid, cat, name)
        elif kind == "col" and self.cur:
            self.cur.editor.insertPlainText(it.data(0, R_NAME))

    def _tree_expanded(self, it):
        kind = it.data(0, R_KIND)
        pid = it.data(0, R_PID)
        if kind == "conn" and pid not in self.meta_sessions and it.childCount() <= 1:
            self.connect_meta(pid)
        elif kind == "obj" and it.data(0, R_CAT) in ("Tabelas", "Views") and it.childCount() == 1 \
                and it.child(0).data(0, R_KIND) == "placeholder":
            name = it.data(0, R_NAME)
            self.meta_call(pid, f"columns\x00{name}", lambda d, c: d.columns(c, name))

    # metadados ----------------------------------------------------
    def meta_session(self, pid) -> Session | None:
        if pid in self.meta_sessions:
            return self.meta_sessions[pid]
        p = self.store.get(pid)
        pw = self.password_for(p)
        if pw is None:
            return None
        s = Session(p, pw, self)
        s.call_done.connect(lambda tag, val, pid=pid: self._meta_done(pid, tag, val))
        s.failed.connect(lambda ctx, msg, pid=pid: self._meta_failed(pid, ctx, msg))
        self.meta_sessions[pid] = s
        return s

    def meta_call(self, pid, tag, fn):
        s = self.meta_session(pid)
        if s:
            if not tag.startswith("ac_"):
                self.statusBar().showMessage("Consultando metadados…")
            s.call(tag, fn)

    def connect_meta(self, pid, refresh=False):
        it = self._conn_item(pid)
        if it:
            it.takeChildren()
            loading = QTreeWidgetItem(["carregando…"])
            loading.setData(0, R_KIND, "placeholder")
            it.addChild(loading)
            it.setExpanded(True)
        self.meta_call(pid, "objects", lambda d, c: d.objects(c))

    def disconnect_meta(self, pid):
        s = self.meta_sessions.pop(pid, None)
        self.meta_objects.pop(pid, None)
        self.columns_cache.pop(pid, None)
        self._loading_objects.discard(pid)
        if s:
            s.close()
            s.deleteLater()
        it = self._conn_item(pid)
        if it:
            it.takeChildren()
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self._refresh_tree_state()

    def _meta_failed(self, pid, ctx, msg):
        self.statusBar().clearMessage()
        if ctx.startswith("ac_"):   # carga silenciosa do autocompletar: não incomoda com popup
            self._loading_objects.discard(pid)
            s = self.meta_sessions.pop(pid, None)
            if s:
                s.close()
                s.deleteLater()
            self.statusBar().showMessage("Autocompletar indisponível: " + msg.splitlines()[0], 6000)
            return
        p = self.store.get(pid)
        s = self.meta_sessions.pop(pid, None)
        if s:
            s.close()
            s.deleteLater()
        it = self._conn_item(pid)
        if it:
            it.takeChildren()
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        if ctx in ("objects", "connect"):
            self.passwords.pop(pid, None)
        QMessageBox.critical(self, "Erro", f"{p.name if p else ''}:\n\n{msg}")
        self._refresh_tree_state()

    def _meta_done(self, pid, tag, val):
        self.statusBar().clearMessage()
        if tag in ("objects", "ac_objects"):
            self._loading_objects.discard(pid)
            self.meta_objects[pid] = val
            it = self._conn_item(pid)
            if it:
                self._fill_objects(it, val)
                it.setExpanded(tag == "objects" or it.isExpanded())
            for tab in self._tabs_of(pid):
                tab.set_objects(val)
            self._refresh_tree_state()
        elif tag.startswith("ac_cols\x00"):
            name = tag.split("\x00", 1)[1]
            cols = [c["name"] for c in val]
            self.columns_cache.setdefault(pid, {})[name.split(".")[-1].upper()] = cols
            for tab in self._tabs_of(pid):
                tab.set_columns(name, cols)
        elif tag.startswith("columns\x00"):
            name = tag.split("\x00", 1)[1]
            it = self._find_obj(pid, name)
            if it:
                it.takeChildren()
                for c in val:
                    nullable = "" if c["nullable"] else " NOT NULL"
                    ch = QTreeWidgetItem([f"{c['name']}  :  {c['type']}{nullable}"])
                    ch.setIcon(0, theme.icon("key", "yellow") if c["pk"] else theme.icon("columns", "muted"))
                    ch.setData(0, R_KIND, "col")
                    ch.setData(0, R_PID, pid)
                    ch.setData(0, R_NAME, c["name"])
                    ch.setForeground(0, QBrush(QColor(theme.c("muted"))))
                    if c.get("default"):
                        ch.setToolTip(0, f"default: {c['default']}")
                    it.addChild(ch)
        elif tag.startswith("source\x00"):
            _, cat, name = tag.split("\x00", 2)
            if not val:
                QMessageBox.information(self, "Código-fonte", "Não disponível para este objeto.")
                return
            self.new_tab(pid, val, "OUTROS")
        elif tag.startswith("struct\x00"):
            name = tag.split("\x00", 1)[1]
            if not val:
                QMessageBox.information(self, "Estrutura", f"Nenhuma coluna encontrada para {name}.")
                return
            p = self.store.get(pid)
            StructureDialog(f"{name}  ({p.name})" if p else name, val, self).show()
        elif tag.startswith("gen\x00"):
            _, name, kind = tag.split("\x00", 2)
            self._gen_done(pid, name, kind, val)

    def _find_obj(self, pid, name):
        it = self._conn_item(pid)
        if not it:
            return None
        for i in range(it.childCount()):
            cat = it.child(i)
            for j in range(cat.childCount()):
                if cat.child(j).data(0, R_NAME) == name and cat.child(j).data(0, R_CAT) in ("Tabelas", "Views"):
                    return cat.child(j)
        return None

    def _fill_objects(self, it, objects):
        it.takeChildren()
        pid = it.data(0, R_PID)
        for cat, names in objects.items():
            c = QTreeWidgetItem([f"{cat} ({len(names)})"])
            c.setData(0, R_KIND, "cat")
            c.setData(0, R_PID, pid)
            c.setIcon(0, theme.icon(CAT_ICONS.get(cat, "folder"), "icon"))
            it.addChild(c)
            for n in names:
                o = QTreeWidgetItem([n])
                o.setData(0, R_KIND, "obj")
                o.setData(0, R_PID, pid)
                o.setData(0, R_CAT, cat)
                o.setData(0, R_NAME, n)
                o.setIcon(0, theme.icon(CAT_ICONS.get(cat, "folder"), CAT_COLORS.get(cat, "icon")))
                if cat in ("Tabelas", "Views"):
                    ph = QTreeWidgetItem(["…"])
                    ph.setData(0, R_KIND, "placeholder")
                    o.addChild(ph)
                c.addChild(o)

    def _tabs_of(self, pid):
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if self.tabs.widget(i).profile_id == pid]

    def ensure_objects(self, pid):
        """Carrega em background a lista de objetos para o autocompletar."""
        if pid in self.meta_objects or pid in self._loading_objects:
            return
        p = self.store.get(pid)
        if p is None:
            return
        if not self.store.get_password(p) and p.user and pid not in self.passwords:
            return  # sem senha salva: espera o usuário conectar (não abre diálogo sozinho)
        self._loading_objects.add(pid)
        self.meta_call(pid, "ac_objects", lambda d, c: d.objects(c))

    def request_columns(self, pid, table):
        objs = self.meta_objects.get(pid) or {}
        full = next((n for cat in ("Tabelas", "Views") for n in objs.get(cat, [])
                     if n.split(".")[-1].upper() == table.upper()), table)
        self.meta_call(pid, f"ac_cols\x00{full}", lambda d, c: d.columns(c, full))

    def show_structure(self, pid, name):
        """Ctrl+clique num nome de tabela/view no editor: abre a estrutura (colunas e tipos)."""
        objs = self.meta_objects.get(pid) or {}
        short = name.split(".")[-1].upper()
        full = next((n for cat in ("Tabelas", "Views") for n in objs.get(cat, [])
                     if n.upper() == name.upper() or n.split(".")[-1].upper() == short), name)
        self.meta_call(pid, f"struct\x00{full}", lambda d, c: d.columns(c, full))

    def object_names(self, pid) -> list[str]:
        objs = self.meta_objects.get(pid) or {}
        names = []
        for cat, lst in objs.items():
            names.extend(n.split(".")[-1] for n in lst)
        return names

    def show_source(self, pid, cat, name):
        self.meta_call(pid, f"source\x00{cat}\x00{name}", lambda d, c: d.source(c, cat, name))

    def gen_sql(self, pid, name, kind):
        self.meta_call(pid, f"gen\x00{name}\x00{kind}", lambda d, c: d.columns(c, name))

    def _gen_done(self, pid, name, kind, cols):
        drv = get_driver(self.store.get(pid).driver)
        t = drv.qualified(name)
        names = [drv.quote(c["name"]) for c in cols]
        if kind == "select":
            sql = "SELECT\n    " + ",\n    ".join(names) + f"\nFROM {t}\nWHERE 1 = 1"
        elif kind == "insert":
            sql = f"INSERT INTO {t} (\n    " + ",\n    ".join(names) + "\n) VALUES (\n    " + \
                  ",\n    ".join(":" + c["name"].lower() for c in cols) + "\n);"
        else:
            pk = [c for c in cols if c["pk"]] or cols[:1]
            sets = [c for c in cols if not c["pk"]]
            sql = f"UPDATE {t} SET\n    " + ",\n    ".join(f"{drv.quote(c['name'])} = :{c['name'].lower()}" for c in sets) + \
                  "\nWHERE " + " AND ".join(f"{drv.quote(c['name'])} = :{c['name'].lower()}" for c in pk) + ";"
        self.new_tab(pid, sql, {"select": "SELECT", "insert": "INSERT", "update": "UPDATE"}[kind])

    # conexões -----------------------------------------------------
    def _connections_changed(self):
        self.populate_tree()
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            tab.refresh_connections()
            if tab.profile_id and not self.store.get(tab.profile_id):
                tab.set_profile(None, ask=False)
            else:
                tab.set_profile(tab.profile_id, ask=False) if tab.session is None else None
        self.update_state()

    def new_connection(self):
        if ConnectionDialog(self.store, parent=self).exec():
            self._connections_changed()

    def edit_connection(self, pid):
        p = self.store.get(pid)
        if ConnectionDialog(self.store, p, self).exec():
            self.passwords.pop(pid, None)
            self.disconnect_meta(pid)
            self._connections_changed()

    def duplicate_connection(self, pid):
        import copy
        import uuid
        p = copy.deepcopy(self.store.get(pid))
        pw = self.store.get_password(self.store.get(pid))
        p.id = uuid.uuid4().hex
        p.name += " (cópia)"
        self.store.upsert(p, pw)
        self._connections_changed()
        self.edit_connection(p.id)

    def delete_connection(self, pid):
        p = self.store.get(pid)
        if any(self.tabs.widget(i).profile_id == pid and self.tabs.widget(i).session for i in range(self.tabs.count())):
            QMessageBox.warning(self, "Excluir", "Feche/desconecte as abas que usam esta conexão antes.")
            return
        if QMessageBox.question(self, "Excluir", f"Excluir a conexão '{p.name}'?") == QMessageBox.Yes:
            self.disconnect_meta(pid)
            self.store.delete(pid)
            self._connections_changed()

    def import_dbjson(self):
        default = str(Path.home() / "Projects/athenas-online/apix/src/db.json")
        path, _ = QFileDialog.getOpenFileName(self, "Importar db.json", default, "JSON (*.json)")
        if not path:
            return
        try:
            n = self.store.import_apix_dbjson(path)
        except Exception as e:
            QMessageBox.critical(self, "Importar", str(e))
            return
        self._connections_changed()
        QMessageBox.information(self, "Importar", f"{n} conexão(ões) importada(s)/atualizada(s).")

    def import_cratos(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar lista do Cratos", str(Path.home() / "Downloads"),
                                              "HTML (*.html *.htm)")
        if not path:
            return
        pw, ok = QInputDialog.getText(self, "Senha padrão", "Senha do SYSDBA para todas as conexões importadas:",
                                      QLineEdit.Password, "masterkey")
        if not ok:
            return
        try:
            created, updated, skipped = self.store.import_cratos_html(path, pw)
        except Exception as e:
            QMessageBox.critical(self, "Importar", str(e))
            return
        self._connections_changed()
        QMessageBox.information(self, "Importar", f"{created} conexão(ões) criada(s), {updated} atualizada(s) "
                                                  f"como CLIENTE, {skipped} ignorada(s) (já cadastrada com o mesmo "
                                                  "banco).\nNenhuma conexão foi aberta.")

    def export_connections(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar conexões", "conexoes.json", "JSON (*.json)")
        if path:
            from dataclasses import asdict
            data = [{k: v for k, v in asdict(p).items() if k != "password"} for p in self.store.profiles]
            Path(path).write_text(json.dumps({"connections": data}, indent=2, ensure_ascii=False), encoding="utf-8")

    # histórico ----------------------------------------------------
    def _add_history(self, pid, conn, sql, ok, elapsed, info):
        self.history.add(conn, sql, ok, elapsed, pid, info)
        self._fill_history()
        for tab in self._tabs_of(pid):
            tab.history_panel.refresh()

    def _fill_history(self, *_):
        q = self.hist_filter.text().lower() if hasattr(self, "hist_filter") else ""
        self.hist_list.clear()
        for h in self.history.items[:500]:
            if q and q not in h["sql"].lower() and q not in h["conn"].lower():
                continue
            one = " ".join(h["sql"].split())
            it = QListWidgetItem(f"{h['ts']}  [{h['conn']}]  {'✓' if h['ok'] else '✗'}  {one[:200]}")
            it.setData(Qt.UserRole, h)
            it.setToolTip(h["sql"][:2000])
            if not h["ok"]:
                it.setForeground(QBrush(QColor(theme.c("red"))))
            self.hist_list.addItem(it)

    def _history_open(self, item):
        h = item.data(Qt.UserRole)
        tab = self.cur or self.new_tab(None)
        tab.append_sql(kind_of(h["sql"]), h["sql"])

    def on_script_finished(self, tab, statements):
        # DDL muda a estrutura: recarrega a árvore se ela estiver aberta
        if tab.profile_id in self.meta_sessions and any(
                leading_words(s.sql, 1)[:1] and leading_words(s.sql, 1)[0] in ("CREATE", "ALTER", "DROP", "RECREATE")
                for s in statements):
            if tab.autocommit:
                self.connect_meta(tab.profile_id, refresh=True)

    def show_shortcuts(self):
        QMessageBox.information(self, "Atalhos", """
F9 / Ctrl+Enter — executar seleção ou comando sob o cursor
Ctrl+F9 / Ctrl+Shift+Enter — executar script inteiro
Ctrl+Alt+C — Commit        Ctrl+Alt+R — Rollback
Ctrl+Shift+R — reconectar a aba ao banco
Ctrl+T — nova aba          Ctrl+O / Ctrl+S — abrir / salvar
Ctrl+Espaço — autocompletar
Ctrl+Shift+F — formatar o SQL selecionado (sem seleção: tudo)
Ctrl+/ — comentar linhas   Ctrl+F / F3 — localizar
Ctrl+B — mostrar/ocultar bancos   Ctrl+J — mostrar/ocultar resultados
Duplo clique em tabela — SELECT das primeiras 100 linhas
Duplo clique em célula — ver valor completo
""".strip())

    # sessão de trabalho -------------------------------------------
    def restore_tabs(self):
        if not SESSION_FILE.exists():
            return
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        if "tabs" in data:
            data = self._migrate_old_tabs(data)
        if not self.a_reopen.isChecked():
            return
        for pid in data.get("open", []):
            if pid is None or self.store.get(pid):
                self.new_tab(pid)
        idx = data.get("current", 0)
        if 0 <= idx < self.tabs.count():
            self.tabs.setCurrentIndex(idx)

    def _migrate_old_tabs(self, data) -> dict:
        """Versão antiga guardava o SQL das abas em open_tabs.json: distribui nas sub-abas de cada banco."""
        merged: dict[str | None, dict[str, list[str]]] = {}
        for t in data.get("tabs", []):
            pid = t.get("profile_id") if self.store.get(t.get("profile_id") or "") else None
            sql = (t.get("sql") or "").strip()
            if not sql:
                continue
            drv = get_driver(self.store.get(pid).driver) if pid else get_driver("firebird")
            sts = split_sql(sql, drv.key, drv.block_prefixes)
            kinds = {kind_of(x.sql) for x in sts} or {"OUTROS"}
            bucket = merged.setdefault(pid, {k: [] for k in SUB_TABS})
            if len(kinds) == 1:                 # tudo do mesmo tipo: mantém o texto como estava
                bucket[kinds.pop()].append(sql)
            else:
                for x in sts:
                    bucket[kind_of(x.sql)].append(x.sql.rstrip(";") + ";")
        for pid, bucket in merged.items():
            ws = load_workspace(pid)
            for k, parts in bucket.items():
                if parts:
                    old = ws["sql"][k].rstrip()
                    ws["sql"][k] = (old + "\n\n" if old else "") + "\n\n".join(parts) + "\n"
            save_workspace(pid, ws)
        new = {"open": [t.get("profile_id") for t in data.get("tabs", [])
                        if t.get("profile_id") is None or self.store.get(t.get("profile_id"))],
               "current": data.get("current", 0)}
        SESSION_FILE.write_text(json.dumps(new), encoding="utf-8")
        return new

    def closeEvent(self, e):
        tabs = [self.tabs.widget(i) for i in range(self.tabs.count())]
        for t in tabs:
            if t.running:
                QMessageBox.information(self, "Sair", "Há comandos em execução. Aguarde ou clique em Parar.")
                e.ignore()
                return
        for t in tabs:
            if t.session and t.session.dirty:
                self.tabs.setCurrentWidget(t)
                if not t.close_session():
                    e.ignore()
                    return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        for t in tabs:
            t.save_workspace()
        SESSION_FILE.write_text(json.dumps({"open": [t.profile_id for t in tabs],
                                            "current": self.tabs.currentIndex()}), encoding="utf-8")
        for t in tabs:
            t.close_session(force=True)
        for s in self.meta_sessions.values():
            s.close()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        self.settings.setValue("max_rows", self.max_rows.value())
        e.accept()
