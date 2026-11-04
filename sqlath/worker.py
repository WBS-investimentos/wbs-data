"""Sessão de banco rodando em thread própria, para a interface nunca travar.

Cada aba de SQL tem a sua Session (conexão + transação próprias), como no
IBExpert. A árvore de objetos usa uma Session separada por banco.
"""
from __future__ import annotations

import datetime
from contextlib import contextmanager
import decimal
import time
import traceback

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from .drivers import get_driver
from .sqlsplit import is_read_only
from .storage import ConnectionProfile

BLOB_READ_LIMIT = 1024 * 1024
DEFAULT_PAGE = 1000              # linhas por página quando nada for informado
_ORPHANS: set = set()


def _norm(v):
    if hasattr(v, "read") and not isinstance(v, (str, bytes)):  # BlobReader do Firebird
        try:
            data = v.read(BLOB_READ_LIMIT)
        finally:
            try:
                v.close()
            except Exception:
                pass
        return data
    if isinstance(v, memoryview):
        return bytes(v)
    return v


class _Worker(QObject):
    connected = Signal(str)                 # versão do servidor / info
    failed = Signal(str, str)               # contexto, mensagem
    statement_done = Signal(dict)
    script_done = Signal(dict)
    more_rows = Signal(list, bool)
    fetch_finished = Signal(str)            # fim de uma busca de página (texto = aviso)
    tx_state = Signal(bool)
    call_done = Signal(str, object)
    closed = Signal()

    def __init__(self, profile: ConnectionProfile, password: str):
        super().__init__()
        self.profile = profile
        self.password = password
        self.driver = get_driver(profile.driver)
        self.conn = None
        self.cursor = None
        self.cancel = False
        self.dirty = False
        self.executing = False   # True enquanto o servidor trabalha (execute ou busca de linhas)

    # ---------------------------------------------------------------
    def _ensure(self):
        if self.conn is None:
            self.conn = self.driver.connect(self.profile, self.password)
            info = ""
            try:
                info = getattr(self.conn, "info", None)
                info = getattr(info, "server_version", "") or getattr(self.conn, "server_version", "")
            except Exception:
                info = ""
            self.connected.emit(str(info or ""))

    @contextmanager
    def _server_work(self):
        """Marca o trecho em que o servidor está trabalhando (o Parar cancela lá)."""
        self.executing = True
        try:
            yield
        finally:
            self.executing = False

    def _set_dirty(self, value: bool):
        if value != self.dirty:
            self.dirty = value
            self.tx_state.emit(value)

    def _close_cursor(self):
        if self.cursor is not None:
            try:
                self.cursor.close()
            except Exception:
                pass
            self.cursor = None

    @Slot()
    def open(self):
        try:
            self._ensure()
        except Exception as e:
            self.failed.emit("connect", _err(e))

    @Slot(list, bool, int, bool)
    def run(self, statements, autocommit, max_rows, stop_on_error):
        self.cancel = False
        t_script = time.perf_counter()
        summary = dict(total=len(statements), ok=0, errors=0, cancelled=False)
        try:
            self._ensure()
        except Exception as e:
            self.failed.emit("connect", _err(e))
            summary["errors"] = 1
            self.script_done.emit(summary)
            return
        for idx, st in enumerate(statements):
            if self.cancel:
                summary["cancelled"] = True
                break
            self._close_cursor()
            info = dict(index=idx, sql=st["sql"], start=st["start"], end=st["end"], columns=None,
                        rows=[], has_more=False, rowcount=-1, error=None)
            t0 = time.perf_counter()
            try:
                cur = self.conn.cursor()
                self.cursor = cur
                with self._server_work():
                    cur.execute(st["sql"])
                if cur.description:
                    info["columns"] = [dict(name=d[0], type=_type_name(d)) for d in cur.description]
                    page = max_rows if max_rows > 0 else DEFAULT_PAGE
                    with self._server_work():   # no Firebird o trabalho pesado do SELECT é aqui
                        rows = cur.fetchmany(page)
                    info["rows"] = [tuple(_norm(v) for v in r) for r in rows]
                    info["has_more"] = len(rows) == page
                else:
                    info["rowcount"] = cur.rowcount
                # SELECT com mais linhas a buscar: não faz commit (fecharia o cursor da rolagem)
                if autocommit and not (info["columns"] is not None and info["has_more"]):
                    self.conn.commit()
                    self._set_dirty(False)
                elif not is_read_only(st["sql"]):
                    self._set_dirty(True)
                summary["ok"] += 1
            except Exception as e:
                info["error"] = _err(e)
                summary["errors"] += 1
                if autocommit:
                    try:
                        self.conn.rollback()
                    except Exception:
                        pass
            info["elapsed"] = time.perf_counter() - t0
            self.statement_done.emit(info)
            if info["error"] and stop_on_error:
                break
        summary["elapsed"] = time.perf_counter() - t_script
        self.script_done.emit(summary)

    @Slot(int)
    def fetch_more(self, n):
        """Próxima página do último SELECT (pedida quando a rolagem chega ao fim)."""
        if self.cursor is None:
            self.more_rows.emit([], False)
            self.fetch_finished.emit("")
            return
        n = n if n > 0 else DEFAULT_PAGE
        try:
            with self._server_work():
                rows = self.cursor.fetchmany(n)
            self.more_rows.emit([tuple(_norm(v) for v in r) for r in rows], len(rows) == n)
        except Exception as e:
            self.more_rows.emit([], False)
            self.failed.emit("fetch", _err(e))
        self.fetch_finished.emit("")

    @Slot()
    def commit(self):
        try:
            self._close_cursor()
            if self.conn is not None:
                self.conn.commit()
            self._set_dirty(False)
            self.call_done.emit("commit", None)
        except Exception as e:
            self.failed.emit("commit", _err(e))

    @Slot()
    def rollback(self):
        try:
            self._close_cursor()
            if self.conn is not None:
                self.conn.rollback()
            self._set_dirty(False)
            self.call_done.emit("rollback", None)
        except Exception as e:
            self.failed.emit("rollback", _err(e))

    @Slot(str, object)
    def call(self, tag, fn):
        """Executa fn(driver, conn) na thread da conexão (metadados etc.)."""
        try:
            self._ensure()
            self.call_done.emit(tag, fn(self.driver, self.conn))
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            self.failed.emit(tag, _err(e))

    @Slot()
    def close(self):
        self._close_cursor()
        if self.conn is not None:
            try:
                self.conn.rollback()
            except Exception:
                pass
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        self.closed.emit()


def _type_name(desc):
    t = desc[1]
    if t is None:
        return ""
    if isinstance(t, type):
        return t.__name__
    return str(getattr(t, "__name__", t))


def _err(e: Exception) -> str:
    msg = str(e).strip() or type(e).__name__
    if not str(e).strip():
        msg += "\n" + traceback.format_exc()
    return msg


class Session(QObject):
    """Fachada usada pela interface. Os sinais são entregues na thread da UI."""

    _req_open = Signal()
    _req_run = Signal(list, bool, int, bool)
    _req_fetch = Signal(int)
    _req_commit = Signal()
    _req_rollback = Signal()
    _req_call = Signal(str, object)
    _req_close = Signal()

    # reemitidos na thread da UI (conectar lambdas aqui é seguro)
    connected = Signal(str)
    failed = Signal(str, str)
    statement_done = Signal(dict)
    script_done = Signal(dict)
    more_rows = Signal(list, bool)
    fetch_finished = Signal(str)
    tx_state = Signal(bool)
    call_done = Signal(str, object)

    def __init__(self, profile: ConnectionProfile, password: str, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.busy = False
        self.thread = QThread()
        self.thread.setObjectName(f"db-{profile.name}")
        self.worker = _Worker(profile, password)
        self.worker.moveToThread(self.thread)
        self._req_open.connect(self.worker.open)
        self._req_run.connect(self.worker.run)
        self._req_fetch.connect(self.worker.fetch_more)
        self._req_commit.connect(self.worker.commit)
        self._req_rollback.connect(self.worker.rollback)
        self._req_call.connect(self.worker.call)
        self._req_close.connect(self.worker.close)
        for name in ("connected", "failed", "statement_done", "script_done", "more_rows", "fetch_finished",
                     "tx_state", "call_done"):
            getattr(self.worker, name).connect(getattr(self, name))
        self.worker.closed.connect(self.thread.quit, Qt.DirectConnection)  # quit() é thread-safe
        self.script_done.connect(self._script_finished)
        self.thread.start()

    @property
    def dirty(self) -> bool:
        return self.worker.dirty

    @property
    def is_open(self) -> bool:
        return self.worker.conn is not None

    def _script_finished(self, _):
        self.busy = False

    def open(self):
        self._req_open.emit()

    def run(self, statements, autocommit, max_rows, stop_on_error=True):
        self.busy = True
        self._req_run.emit(statements, autocommit, max_rows, stop_on_error)

    def fetch_more(self, n):
        self._req_fetch.emit(n)

    def commit(self):
        self._req_commit.emit()

    def rollback(self):
        self._req_rollback.emit()

    def call(self, tag, fn):
        self._req_call.emit(tag, fn)

    def cancel(self):
        """Pede para parar: interrompe o script entre comandos e tenta cancelar o atual."""
        self.worker.cancel = True       # para entre comandos e entre blocos da busca
        conn = self.worker.conn
        if conn is not None and self.worker.executing:   # consulta rodando no servidor: cancela lá
            try:
                self.worker.driver.cancel(conn)
            except Exception:
                pass

    def close(self, wait_ms=5000):
        if self.thread.isRunning():
            self._req_close.emit()
            if not self.thread.wait(wait_ms):
                # servidor lento para desconectar: mantém a thread viva até terminar
                _ORPHANS.add(self.thread)
                self.thread.setParent(None)
                self.thread.finished.connect(lambda t=self.thread: _ORPHANS.discard(t))


def to_display(v) -> str:
    if v is None:
        return "<null>"
    if isinstance(v, bytes):
        return f"(BLOB {len(v)} bytes)"
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, decimal.Decimal):
        return format(v, "f")
    if isinstance(v, float):
        return repr(v)
    return str(v)
