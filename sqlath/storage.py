"""Persistência dos perfis de conexão e do histórico de SQL.

Os perfis ficam em ~/.config/sqlath/connections.json. As senhas vão para o
chaveiro do sistema (Secret Service / KWallet) via `keyring`; se não houver
chaveiro disponível, ficam no próprio JSON (campo "password").
"""
from __future__ import annotations

import json
import os
from html.parser import HTMLParser
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import cores

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sqlath"
CONNECTIONS_FILE = CONFIG_DIR / "connections.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
KEYRING_SERVICE = "sqlath"
HISTORY_LIMIT = 5000

# do mais crítico para o menos crítico
ENVIRONMENTS = ["Cliente", "Produção", "Homologação", "Desenvolvimento", "Teste", "Local"]
# cores do Skorpa (sqlath/cores.py); iguais nos dois temas — ver docs/cores.md
ENV_COLORS = {
    "Cliente": cores.DARK["negativeInputColor"],
    "Produção": cores.DARK["negativeColor"],
    "Homologação": cores.DARK["accentColor"],
    "Desenvolvimento": cores.DARK["blueColor"],
    "Teste": cores.DARK["tealColor"],
    "Local": cores.DARK["positiveColor"],
}


@dataclass
class ConnectionProfile:
    name: str
    driver: str = "firebird"
    environment: str = "Desenvolvimento"
    host: str = ""
    port: str = ""
    database: str = ""
    user: str = ""
    charset: str = "UTF8"
    role: str = ""
    options: str = ""          # parâmetros extras "chave=valor;chave=valor"
    read_only: bool = False
    autocommit: bool = False
    notes: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    password: str = field(default="", repr=False)  # só em memória / fallback

    @property
    def is_production(self) -> bool:
        return self.environment == "Produção"

    @property
    def is_client(self) -> bool:
        """Banco de cliente: o ambiente mais crítico de todos."""
        return self.environment == "Cliente"

    @property
    def is_critical(self) -> bool:
        return self.is_client or self.is_production


def _keyring():
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
        return keyring
    except Exception:
        return None


class ConnectionStore:
    def __init__(self, path: Path = CONNECTIONS_FILE):
        self.path = path
        self.profiles: list[ConnectionProfile] = []
        self.load()

    def load(self):
        self.profiles = []
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        known = ConnectionProfile.__dataclass_fields__
        for item in data.get("connections", []):
            self.profiles.append(ConnectionProfile(**{k: v for k, v in item.items() if k in known}))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        kr = _keyring()
        items = []
        for p in self.profiles:
            d = asdict(p)
            if kr is not None:
                d.pop("password")
            items.append(d)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"connections": items}, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def get(self, profile_id: str) -> ConnectionProfile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def upsert(self, profile: ConnectionProfile, password: str | None = None):
        if password is not None:
            self.set_password(profile, password)
        for i, p in enumerate(self.profiles):
            if p.id == profile.id:
                self.profiles[i] = profile
                break
        else:
            self.profiles.append(profile)
        self.save()

    def delete(self, profile_id: str):
        kr = _keyring()
        if kr is not None:
            try:
                kr.delete_password(KEYRING_SERVICE, profile_id)
            except Exception:
                pass
        self.profiles = [p for p in self.profiles if p.id != profile_id]
        self.save()

    def set_password(self, profile: ConnectionProfile, password: str):
        kr = _keyring()
        if kr is not None:
            kr.set_password(KEYRING_SERVICE, profile.id, password)
            profile.password = ""
        else:
            profile.password = password

    def get_password(self, profile: ConnectionProfile) -> str:
        kr = _keyring()
        if kr is not None:
            try:
                return kr.get_password(KEYRING_SERVICE, profile.id) or profile.password
            except Exception:
                return profile.password
        return profile.password

    # ---- exportar / importar uma conexão (JSON) -----------------------
    def export_profile(self, profile_id: str, path: str | Path, include_password: bool = False):
        p = self.get(profile_id)
        d = {k: v for k, v in asdict(p).items() if k not in ("id", "password")}
        if include_password:
            d["password"] = self.get_password(p)
        data = {"skorpa_data_connection": 1, "connection": d}
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if include_password:
            os.chmod(path, 0o600)

    @staticmethod
    def read_profiles_file(path: str | Path) -> list[tuple[ConnectionProfile, str | None]]:
        """Lê um arquivo de conexão. Aceita o formato de uma conexão
        ({"connection": {...}}), a lista ({"connections": [...]}) ou o objeto solto.
        Devolve (perfil com id novo, senha ou None)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("connection"), dict):
            items = [data["connection"]]
        elif isinstance(data, dict) and isinstance(data.get("connections"), list):
            items = data["connections"]
        elif isinstance(data, dict) and "name" in data:
            items = [data]
        else:
            raise ValueError("Arquivo não parece uma conexão do WBS Data.")
        known = ConnectionProfile.__dataclass_fields__
        out = []
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError("Conexão sem nome no arquivo.")
            pw = item.get("password")
            fields = {k: v for k, v in item.items() if k in known and k not in ("id", "password")}
            prof = ConnectionProfile(**fields)
            if prof.environment not in ENVIRONMENTS:
                prof.environment = "Desenvolvimento"
            out.append((prof, pw if isinstance(pw, str) and pw else None))
        return out

    # ---- importação ---------------------------------------------------
    def import_cratos_html(self, path, password: str, user: str = "SYSDBA", environment: str = "Cliente"):
        return _import_cratos(self, path, password, user, environment)

    def import_apix_dbjson(self, path: str | Path) -> int:
        """Importa o formato do athenas-online/apix/src/db.json.

        { "chave": {"sub", "host", "user", "pass", "db", "port"}, ... }
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        env_map = {"homolog": "Homologação", "localhost": "Local", "prod": "Produção",
                   "producao": "Produção", "cliente": "Cliente", "dev": "Desenvolvimento", "teste": "Teste", "test": "Teste"}
        count = 0
        for key, cfg in data.items():
            host = cfg.get("host", "")
            if host == "host.docker.internal":
                # só resolve dentro de containers; no host é a própria máquina
                host = "172.19.0.1"
            existing = next((p for p in self.profiles if p.name == key), None)
            prof = existing or ConnectionProfile(name=key)
            prof.driver = "firebird"
            prof.environment = env_map.get(str(cfg.get("sub", "")).lower(), "Desenvolvimento")
            prof.host = host
            prof.port = str(cfg.get("port", ""))
            prof.database = cfg.get("db", "")
            prof.user = cfg.get("user", "")
            prof.notes = f"Importado de {path} (sub={cfg.get('sub', '')})"
            self.upsert(prof, cfg.get("pass", ""))
            count += 1
        return count


CRATOS_MARK = "cadastrado no Cratos por"


def _import_cratos(store: "ConnectionStore", path, password: str, user: str = "SYSDBA",
                   environment: str = "Cliente") -> tuple[int, int]:
    """Cadastra (ou atualiza, pelo nome) as conexões da lista do Cratos. NÃO conecta em nada."""
    entries = parse_cratos_html(path)
    by_name = {p.name: p for p in store.profiles}
    kr = _keyring()
    created = updated = 0
    skipped = 0
    for e in entries:
        name = e["sub"]
        prof = by_name.get(name)
        if prof is not None and CRATOS_MARK not in prof.notes:
            # nome já usado por uma conexão sua: nunca sobrescreve
            same = (prof.host, str(prof.port), prof.database) == (e["host"], e["port"], e["path"])
            if same:
                skipped += 1
                continue
            name = f"{e['sub']} (cliente)"
            prof = by_name.get(name)
        if prof is None:
            prof = ConnectionProfile(name=name)
            store.profiles.append(prof)
            by_name[prof.name] = prof
            created += 1
        else:
            updated += 1
        prof.driver = "firebird"
        prof.environment = environment
        prof.host = e["host"]
        prof.port = e["port"]
        prof.database = e["path"]
        prof.user = user
        prof.notes = (f"Hospedagem: {e['hosting'] or '-'} | {CRATOS_MARK}: {e['registered_by'] or '-'}\n"
                      f"Importado de {Path(path).name}")
        if kr is not None:
            kr.set_password(KEYRING_SERVICE, prof.id, password)
            prof.password = ""
        else:
            prof.password = password
    store.save()   # um único save no fim (são centenas de conexões)
    return created, updated, skipped


class _CratosTable(HTMLParser):
    """Lê a tabela da página Connection do Cratos (salva como HTML)."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
            self.rows.append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._row.append(self._cell)

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None and data.strip():
            self._cell.append(data.strip())


def parse_cratos_html(path: str | Path) -> list[dict]:
    p = _CratosTable()
    p.feed(Path(path).read_text(encoding="utf-8", errors="replace"))
    rows = [[" ".join(c) for c in r] for r in p.rows]
    if not rows:
        return []
    header = [h.lower() for h in rows[0]]
    need = ["sub", "host", "caminho", "porta"]
    if not all(n in header for n in need):
        raise ValueError(f"Tabela não reconhecida. Colunas encontradas: {rows[0]}")
    idx = {n: header.index(n) for n in header}
    out = []
    for r in rows[1:]:
        if len(r) < len(header):
            continue
        get = lambda n: r[idx[n]].strip() if n in idx else ""
        out.append(dict(sub=get("sub"), host=get("host"), path=get("caminho"), port=get("porta"),
                        hosting=get("hospservidor"), registered_by=get("usoreg")))
    return out


class History:
    def __init__(self, path: Path = HISTORY_FILE):
        self.path = path
        self.items: list[dict] = []
        if path.exists():
            try:
                self.items = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.items = []

    def add(self, connection_name: str, sql: str, ok: bool, elapsed: float, profile_id: str | None = None,
            info: str = ""):
        self.items.insert(0, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "conn": connection_name,
                              "pid": profile_id, "sql": sql, "ok": ok, "elapsed": round(elapsed, 3),
                              "info": info})
        del self.items[HISTORY_LIMIT:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def for_profile(self, profile_id: str | None, profile_name: str = "") -> list[dict]:
        """Execuções de um banco (entradas antigas, sem id, casam pelo nome da conexão)."""
        if not profile_id:            # aba sem banco não executa nada
            return []
        return [h for h in self.items
                if h.get("pid") == profile_id or (not h.get("pid") and profile_name and h.get("conn") == profile_name)]


# ---- área de trabalho por banco: o SQL de cada sub-aba fica salvo -------------
WORKSPACES_DIR = CONFIG_DIR / "workspaces"
SUB_TABS = ["SELECT", "INSERT", "UPDATE", "DELETE", "OUTROS"]
NO_DB_KEY = "_sem_banco"


def _workspace_path(profile_id: str | None) -> Path:
    key = profile_id or NO_DB_KEY
    if not all(ch.isalnum() or ch in "_-" for ch in key):   # ids são uuid hex; nada de caminhos
        raise ValueError(f"id de workspace inválido: {key!r}")
    return WORKSPACES_DIR / f"{key}.json"


def load_workspace(profile_id: str | None) -> dict:
    """{"sql": {sub_aba: texto}, "current": sub_aba, "cursor": {sub_aba: posição}}"""
    path = _workspace_path(profile_id)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    sql = data.get("sql") or {}
    return {"sql": {k: sql.get(k, "") for k in SUB_TABS},
            "current": data.get("current") if data.get("current") in SUB_TABS else "SELECT",
            "cursor": data.get("cursor") or {}}


def save_workspace(profile_id: str | None, data: dict):
    path = _workspace_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def workspace_is_empty(profile_id: str | None) -> bool:
    return not any(t.strip() for t in load_workspace(profile_id)["sql"].values())
