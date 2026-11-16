"""Cadastro de conexões."""
from __future__ import annotations

import copy

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from . import theme
from .drivers import DRIVERS, get_driver
from .storage import ENV_COLORS, ENVIRONMENTS, ConnectionProfile, ConnectionStore

_RUNNING: set = set()  # mantém threads de teste vivas mesmo se o diálogo fechar

FIREBIRD_CHARSETS = ["UTF8", "WIN1252", "ISO8859_1", "NONE", "UNICODE_FSS", "WIN1251", "ASCII"]


class _TestThread(QThread):
    done = Signal(bool, str)

    def __init__(self, profile, password):
        super().__init__()
        self.profile, self.password = profile, password

    def run(self):
        try:
            conn = get_driver(self.profile.driver).connect(self.profile, self.password)
            try:
                conn.close()
            except Exception:
                pass
            self.done.emit(True, "Conexão OK!")
        except Exception as e:
            self.done.emit(False, str(e))


class ConnectionDialog(QDialog):
    def __init__(self, store: ConnectionStore, profile: ConnectionProfile | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.is_new = profile is None
        self.profile = copy.deepcopy(profile) if profile else ConnectionProfile(name="")
        self.setWindowTitle("Nova conexão" if self.is_new else f"Editar conexão — {self.profile.name}")
        self.setMinimumWidth(560)
        self._tester = None

        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

        self.name = QLineEdit(self.profile.name)
        form.addRow("Nome*", self.name)

        self.env = QComboBox()
        for e in ENVIRONMENTS:
            self.env.addItem(e)
        self.env.setCurrentText(self.profile.environment)
        self.env.currentTextChanged.connect(self._env_changed)
        form.addRow("Ambiente", self.env)

        self.driver = QComboBox()
        for d in DRIVERS.values():
            self.driver.addItem(d.label, d.key)
        self.driver.setCurrentIndex(max(0, self.driver.findData(self.profile.driver)))
        self.driver.currentIndexChanged.connect(self._driver_changed)
        form.addRow("SGBD", self.driver)

        hp = QHBoxLayout()
        self.host = QLineEdit(self.profile.host)
        self.host.setPlaceholderText("servidor ou IP (vazio = local/embedded)")
        self.port = QLineEdit(self.profile.port)
        self.port.setMaximumWidth(90)
        hp.addWidget(self.host, 1)
        hp.addWidget(QLabel("Porta"))
        hp.addWidget(self.port)
        self.host_row = QWidget()
        self.host_row.setLayout(hp)
        hp.setContentsMargins(0, 0, 0, 0)
        form.addRow("Servidor", self.host_row)

        dbl = QHBoxLayout()
        dbl.setContentsMargins(0, 0, 0, 0)
        self.database = QLineEdit(self.profile.database)
        browse = QToolButton()
        browse.setText("…")
        browse.clicked.connect(self._browse)
        dbl.addWidget(self.database, 1)
        dbl.addWidget(browse)
        dbw = QWidget()
        dbw.setLayout(dbl)
        self.db_label = QLabel("Banco")
        form.addRow(self.db_label, dbw)

        self.user = QLineEdit(self.profile.user)
        form.addRow("Usuário", self.user)

        pw = QHBoxLayout()
        pw.setContentsMargins(0, 0, 0, 0)
        self.password = QLineEdit(store.get_password(self.profile) if not self.is_new else "")
        self.password.setEchoMode(QLineEdit.Password)
        show = QToolButton()
        show.setText("👁")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        pw.addWidget(self.password, 1)
        pw.addWidget(show)
        pww = QWidget()
        pww.setLayout(pw)
        form.addRow("Senha", pww)

        self.charset = QComboBox()
        self.charset.setEditable(True)
        self.charset.setCurrentText(self.profile.charset)
        form.addRow("Charset", self.charset)

        self.role = QLineEdit(self.profile.role)
        self.role_label = QLabel("Role")
        form.addRow(self.role_label, self.role)

        self.options = QLineEdit(self.profile.options)
        self.options.setPlaceholderText("parâmetros extras: chave=valor; chave=valor")
        form.addRow("Opções", self.options)

        self.read_only = QCheckBox("Somente leitura (bloqueia INSERT/UPDATE/DELETE/DDL)")
        self.read_only.setChecked(self.profile.read_only)
        form.addRow("", self.read_only)
        self.autocommit = QCheckBox("Auto-commit ligado por padrão nas abas deste banco")
        self.autocommit.setChecked(self.profile.autocommit)
        form.addRow("", self.autocommit)

        self.notes = QPlainTextEdit(self.profile.notes)
        self.notes.setMaximumHeight(60)
        form.addRow("Observações", self.notes)

        self.env_banner = QLabel()
        self.env_banner.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.env_banner)

        self.test_label = QLabel()
        self.test_label.setWordWrap(True)
        lay.addWidget(self.test_label)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.btn_test = QPushButton("Testar conexão")
        bb.addButton(self.btn_test, QDialogButtonBox.ActionRole)
        self.btn_test.clicked.connect(self._test)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._driver_changed(initial=True)
        self._env_changed(self.env.currentText())

    def _env_changed(self, env):
        color = ENV_COLORS.get(env, "#555")
        txt = {"Cliente": "⛔ CLIENTE — o mais crítico: alterações e commit exigem digitar o nome da conexão; "
                          "auto-commit bloqueado",
               "Produção": "⚠ PRODUÇÃO — comandos de alteração pedirão confirmação"}.get(env, env)
        self.env_banner.setText(txt)
        self.env_banner.setStyleSheet(f"background:{color}; color:white; padding:4px; font-weight:bold;")

    def _driver_changed(self, *_, initial=False):
        d = get_driver(self.driver.currentData())
        self.host_row.setEnabled(d.needs_host)
        self.db_label.setText(d.database_label)
        if not initial:
            self.port.setText(d.default_port)
        current = self.charset.currentText()
        self.charset.clear()
        if d.key == "firebird":
            self.charset.addItems(FIREBIRD_CHARSETS)
        elif d.key == "mysql":
            self.charset.addItems(["utf8mb4", "utf8", "latin1"])
        elif d.key == "postgres":
            self.charset.addItems(["", "UTF8", "LATIN1", "WIN1252"])
        elif d.key == "mssql":
            self.charset.addItems(["UTF-8", "CP1252"])
        else:
            self.charset.addItems([""])
        if initial:
            self.charset.setCurrentText(current)
        is_fb = d.key == "firebird"
        self.role.setVisible(is_fb)
        self.role_label.setVisible(is_fb)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Arquivo do banco", "",
                                              "Bancos (*.fdb *.gdb *.FDB *.GDB *.db *.sqlite *.sqlite3);;Todos (*)")
        if path:
            self.database.setText(path)

    def _collect(self) -> ConnectionProfile:
        p = self.profile
        p.name = self.name.text().strip()
        p.environment = self.env.currentText()
        p.driver = self.driver.currentData()
        p.host = self.host.text().strip()
        p.port = self.port.text().strip()
        p.database = self.database.text().strip()
        p.user = self.user.text().strip()
        p.charset = self.charset.currentText().strip()
        p.role = self.role.text().strip()
        p.options = self.options.text().strip()
        p.read_only = self.read_only.isChecked()
        p.autocommit = self.autocommit.isChecked()
        p.notes = self.notes.toPlainText()
        return p

    def _test(self):
        p = copy.deepcopy(self._collect())
        self.btn_test.setEnabled(False)
        self.test_label.setText("Conectando…")
        self.test_label.setStyleSheet("")
        self._tester = _TestThread(p, self.password.text())
        self._tester.done.connect(self._test_done)
        t = self._tester
        _RUNNING.add(t)
        t.finished.connect(lambda: _RUNNING.discard(t))
        t.start()

    def _test_done(self, ok, msg):
        self.btn_test.setEnabled(True)
        self.test_label.setText(msg)
        self.test_label.setStyleSheet("color:#2e7d32; font-weight:bold;" if ok else "color:#c62828;")

    def _save(self):
        p = self._collect()
        if not p.name:
            QMessageBox.warning(self, "Cadastro", "Informe um nome para a conexão.")
            return
        if any(o.name == p.name and o.id != p.id for o in self.store.profiles):
            QMessageBox.warning(self, "Cadastro", f"Já existe uma conexão chamada '{p.name}'.")
            return
        if not p.database:
            QMessageBox.warning(self, "Cadastro", "Informe o banco de dados.")
            return
        self.store.upsert(p, self.password.text())
        self.accept()


class StructureDialog(QDialog):
    """Estrutura de uma tabela/view: colunas, tipos, nulidade, default e PK (Ctrl+clique no editor)."""

    def __init__(self, title, columns, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Estrutura — {title}")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(760, min(140 + 26 * len(columns), 720))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{title}</b> — {len(columns)} coluna(s)"))

        heads = ["#", "Coluna", "Tipo", "Nulo", "Default"]
        t = QTableWidget(len(columns), len(heads))
        t.setHorizontalHeaderLabels(heads)
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setAlternatingRowColors(True)
        for r, c in enumerate(columns):
            name = QTableWidgetItem(c["name"])
            if c.get("pk"):
                name.setIcon(theme.icon("key", "yellow"))
                name.setToolTip("Chave primária")
            vals = [QTableWidgetItem(str(r + 1)), name, QTableWidgetItem(c["type"]),
                    QTableWidgetItem("" if c.get("nullable", True) else "NOT NULL"),
                    QTableWidgetItem(c.get("default") or "")]
            vals[0].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            for col, it in enumerate(vals):
                t.setItem(r, col, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        lay.addWidget(t)

        bar = QHBoxLayout()
        b_names = QPushButton("Copiar nomes das colunas")
        b_names.clicked.connect(lambda: QGuiApplication.clipboard().setText(
            ", ".join(c["name"] for c in columns)))
        b_all = QPushButton("Copiar estrutura")
        b_all.clicked.connect(lambda: QGuiApplication.clipboard().setText("\n".join(
            "\t".join([c["name"], c["type"], "" if c.get("nullable", True) else "NOT NULL",
                       c.get("default") or "", "PK" if c.get("pk") else ""]) for c in columns)))
        bar.addWidget(b_names)
        bar.addWidget(b_all)
        bar.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bar.addWidget(bb)
        lay.addLayout(bar)
