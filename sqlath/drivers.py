"""Camada de drivers: conexão DB-API + consultas de metadados por SGBD."""
from __future__ import annotations

import codecs
import re
import sqlite3

from .storage import ConnectionProfile

_SIMPLE_UPPER = re.compile(r"^[A-Z_][A-Z0-9_$]*$")
_SIMPLE_LOWER = re.compile(r"^[a-z_][a-z0-9_$]*$")


def _tolerant_codec(name: str):
    """Codec que decodifica com `base` e, se falhar, cai para WIN1252/Latin-1.

    Bancos Firebird antigos costumam ter colunas com charset NONE guardando
    texto WIN1252; com a conexão em UTF8 o driver quebraria nesses bytes.
    """
    if not name.startswith("sqlath_tolerant_"):
        return None
    base = codecs.lookup(name[len("sqlath_tolerant_"):])

    def decode(data, errors="strict"):
        data = bytes(data)
        for enc in (base.name, "cp1252"):
            try:
                return data.decode(enc), len(data)
            except UnicodeDecodeError:
                pass
        return data.decode("latin-1"), len(data)

    return codecs.CodecInfo(name=name, encode=base.encode, decode=decode)


codecs.register(_tolerant_codec)


def parse_options(text: str) -> dict:
    opts = {}
    for part in re.split(r"[;\n]", text or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            opts[k.strip()] = v.strip()
    return opts


def _rows(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params) if params else cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()]
    finally:
        try:
            cur.close()
        except Exception:
            pass


class Driver:
    key = ""
    label = ""
    default_port = ""
    needs_host = True
    database_label = "Banco"
    categories: list[str] = ["Tabelas", "Views"]
    block_prefixes: tuple[str, ...] = ()   # início de comandos com BEGIN..END internos

    def connect(self, p: ConnectionProfile, password: str):
        raise NotImplementedError

    def quote(self, name: str) -> str:
        return name if _SIMPLE_LOWER.match(name) or _SIMPLE_UPPER.match(name) else '"%s"' % name.replace('"', '""')

    def qualified(self, name: str) -> str:
        return ".".join(self.quote(n) for n in name.split(".")) if "." in name else self.quote(name)

    def top_sql(self, table: str, n: int) -> str:
        return f"SELECT * FROM {self.qualified(table)} LIMIT {n}"

    def objects(self, conn) -> dict[str, list[str]]:
        return {}

    def cancel(self, conn):
        """Cancela a consulta em andamento (chamado de outra thread)."""
        fn = getattr(conn, "cancel", None)
        if callable(fn):
            fn()

    def columns(self, conn, table: str) -> list[dict]:
        return []

    def source(self, conn, category: str, name: str) -> str | None:
        return None

    # --- SQL padrão: "a tabela existe?" / "a coluna existe?" (menu do editor) ---
    fold = ""   # "upper"/"lower": como o banco guarda nomes sem aspas

    def _ident(self, name: str) -> str:
        """Nome digitado como o catálogo guarda (sem aspas, com a caixa do banco)."""
        name = name.strip()
        if len(name) > 1 and name[0] == name[-1] and name[0] in "\"`[":
            return name[1:-1]
        if self.fold == "upper":
            return name.upper()
        if self.fold == "lower" and (name.isupper() or name.islower()):   # MiXtO: criado com aspas
            return name.lower()
        return name

    def _split(self, table: str) -> tuple[str, str]:
        schema, _, name = table.strip().rpartition(".")
        return (self._ident(schema) if schema else ""), self._ident(name)

    @staticmethod
    def _lit(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _exists(from_where: str) -> str:
        return f"SELECT CASE WHEN COUNT(*) > 0 THEN 'SIM' ELSE 'NAO' END AS EXISTE\n  FROM {from_where}"

    def table_exists_sql(self, table: str) -> str:
        schema, name = self._split(table)
        where = f"table_name = {self._lit(name)}"
        if schema:
            where = f"table_schema = {self._lit(schema)}\n   AND " + where
        return self._exists(f"information_schema.tables\n WHERE {where}")

    def column_exists_sql(self, table: str, column: str) -> str:
        schema, name = self._split(table)
        where = f"table_name = {self._lit(name)}\n   AND column_name = {self._lit(self._ident(column))}"
        if schema:
            where = f"table_schema = {self._lit(schema)}\n   AND " + where
        return self._exists(f"information_schema.columns\n WHERE {where}")

    def table_ddl(self, conn, table: str) -> str:
        cols = self.columns(conn, table)
        lines = []
        for c in cols:
            line = f"    {self.quote(c['name'])} {c['type']}"
            if c.get("default"):
                d = c["default"].strip()
                line += " " + (d if d.upper().startswith("DEFAULT") else "DEFAULT " + d)
            if not c.get("nullable", True):
                line += " NOT NULL"
            lines.append(line)
        pk = [self.quote(c["name"]) for c in cols if c.get("pk")]
        if pk:
            lines.append(f"    PRIMARY KEY ({', '.join(pk)})")
        return f"CREATE TABLE {self.qualified(table)} (\n" + ",\n".join(lines) + "\n);"


# --------------------------------------------------------------------------
class FirebirdDriver(Driver):
    fold = "upper"

    def table_exists_sql(self, table):
        name = self._split(table)[1]
        return self._exists(f"RDB$RELATIONS\n WHERE RDB$RELATION_NAME = {self._lit(name)}")

    def column_exists_sql(self, table, column):
        name = self._split(table)[1]
        return self._exists(f"RDB$RELATION_FIELDS\n WHERE RDB$RELATION_NAME = {self._lit(name)}"
                            f"\n   AND RDB$FIELD_NAME = {self._lit(self._ident(column))}")

    key = "firebird"
    label = "Firebird / InterBase"
    default_port = "3050"
    database_label = "Arquivo / alias"
    categories = ["Tabelas", "Views", "Procedures", "Triggers", "Generators", "Domínios", "Funções"]
    block_prefixes = ("EXECUTE BLOCK", "CREATE PROCEDURE", "CREATE OR ALTER PROCEDURE", "ALTER PROCEDURE",
                      "RECREATE PROCEDURE", "CREATE TRIGGER", "CREATE OR ALTER TRIGGER", "ALTER TRIGGER",
                      "RECREATE TRIGGER", "CREATE FUNCTION", "CREATE OR ALTER FUNCTION", "ALTER FUNCTION",
                      "RECREATE FUNCTION", "CREATE PACKAGE", "CREATE OR ALTER PACKAGE", "RECREATE PACKAGE",
                      "ALTER PACKAGE")

    def connect(self, p, password):
        from firebird.driver import connect

        dsn = p.database
        if p.host:
            dsn = f"{p.host}/{p.port}:{p.database}" if p.port else f"{p.host}:{p.database}"
        kwargs = dict(user=p.user or None, password=password or None, charset=p.charset or None,
                      role=p.role or None)
        opts = parse_options(p.options)
        if "sql_dialect" in opts:
            kwargs["sql_dialect"] = int(opts["sql_dialect"])
        conn = connect(dsn, **{k: v for k, v in kwargs.items() if v is not None})
        # cursores herdam conn._encoding: troca pelo codec tolerante
        conn._encoding = "sqlath_tolerant_" + codecs.lookup(conn._encoding).name
        return conn

    def quote(self, name):
        return name if _SIMPLE_UPPER.match(name) else '"%s"' % name.replace('"', '""')

    def cancel(self, conn):
        # fb_cancel_operation: feito para ser chamado de outra thread durante a execução
        from firebird.driver.types import CancelType
        conn._att.cancel_operation(CancelType.RAISE)

    def top_sql(self, table, n):
        return f"SELECT FIRST {n} * FROM {self.quote(table)}"

    def objects(self, conn):
        sys0 = "COALESCE(RDB$SYSTEM_FLAG, 0) = 0"
        q = {
            "Tabelas": f"SELECT TRIM(RDB$RELATION_NAME) FROM RDB$RELATIONS WHERE {sys0} AND RDB$VIEW_BLR IS NULL ORDER BY 1",
            "Views": f"SELECT TRIM(RDB$RELATION_NAME) FROM RDB$RELATIONS WHERE {sys0} AND RDB$VIEW_BLR IS NOT NULL ORDER BY 1",
            "Procedures": f"SELECT TRIM(RDB$PROCEDURE_NAME) FROM RDB$PROCEDURES WHERE {sys0} ORDER BY 1",
            "Triggers": f"SELECT TRIM(RDB$TRIGGER_NAME) FROM RDB$TRIGGERS WHERE {sys0} ORDER BY 1",
            "Generators": f"SELECT TRIM(RDB$GENERATOR_NAME) FROM RDB$GENERATORS WHERE {sys0} ORDER BY 1",
            "Domínios": f"SELECT TRIM(RDB$FIELD_NAME) FROM RDB$FIELDS WHERE {sys0} AND RDB$FIELD_NAME NOT STARTING WITH 'RDB$' ORDER BY 1",
            "Funções": f"SELECT TRIM(RDB$FUNCTION_NAME) FROM RDB$FUNCTIONS WHERE {sys0} ORDER BY 1",
        }
        out = {}
        for cat, sql in q.items():
            try:
                out[cat] = [r[0] for r in _rows(conn, sql)]
            except Exception:
                out[cat] = []
        conn.commit()
        return out

    @staticmethod
    def _type(ftype, sub, length, charlen, prec, scale, segment=None):
        scale = scale or 0
        if ftype in (7, 8, 16, 26) and (scale < 0 or (sub or 0) in (1, 2)):
            kind = "DECIMAL" if sub == 2 else "NUMERIC"
            return f"{kind}({prec or {7: 4, 8: 9, 16: 18, 26: 38}[ftype]},{-scale})"
        simple = {7: "SMALLINT", 8: "INTEGER", 16: "BIGINT", 26: "INT128", 10: "FLOAT", 27: "DOUBLE PRECISION",
                  12: "DATE", 13: "TIME", 35: "TIMESTAMP", 23: "BOOLEAN", 24: "DECFLOAT(16)", 25: "DECFLOAT(34)",
                  28: "TIME WITH TIME ZONE", 29: "TIMESTAMP WITH TIME ZONE"}
        if ftype in simple:
            return simple[ftype]
        if ftype == 14:
            return f"CHAR({charlen or length})"
        if ftype == 37:
            return f"VARCHAR({charlen or length})"
        if ftype == 261:
            return f"BLOB SUB_TYPE {sub or 0}" + (" SEGMENT SIZE %d" % segment if segment else "")
        return f"TYPE_{ftype}"

    def columns(self, conn, table):
        rows = _rows(conn, """
            SELECT TRIM(rf.RDB$FIELD_NAME), f.RDB$FIELD_TYPE, f.RDB$FIELD_SUB_TYPE, f.RDB$FIELD_LENGTH,
                   f.RDB$CHARACTER_LENGTH, f.RDB$FIELD_PRECISION, f.RDB$FIELD_SCALE,
                   COALESCE(rf.RDB$NULL_FLAG, f.RDB$NULL_FLAG, 0), TRIM(rf.RDB$FIELD_SOURCE),
                   COALESCE(rf.RDB$DEFAULT_SOURCE, f.RDB$DEFAULT_SOURCE), f.RDB$COMPUTED_SOURCE
              FROM RDB$RELATION_FIELDS rf
              JOIN RDB$FIELDS f ON f.RDB$FIELD_NAME = rf.RDB$FIELD_SOURCE
             WHERE rf.RDB$RELATION_NAME = ?
             ORDER BY rf.RDB$FIELD_POSITION""", (table,))
        pk = {r[0] for r in _rows(conn, """
            SELECT TRIM(s.RDB$FIELD_NAME) FROM RDB$RELATION_CONSTRAINTS c
              JOIN RDB$INDEX_SEGMENTS s ON s.RDB$INDEX_NAME = c.RDB$INDEX_NAME
             WHERE c.RDB$RELATION_NAME = ? AND c.RDB$CONSTRAINT_TYPE = 'PRIMARY KEY'""", (table,))}
        conn.commit()
        cols = []
        for name, ft, sub, ln, cl, pr, sc, notnull, source, default, computed in rows:
            typ = self._type(ft, sub, ln, cl, pr, sc)
            if source and not source.startswith("RDB$"):
                typ = f"{source} /* {typ} */"
            if computed:
                typ = f"COMPUTED BY {computed}"
            cols.append(dict(name=name, type=typ, nullable=not notnull, default=_text(default) or "",
                             pk=name in pk))
        return cols

    def source(self, conn, category, name):
        try:
            if category == "Views":
                src = _rows(conn, "SELECT RDB$VIEW_SOURCE FROM RDB$RELATIONS WHERE RDB$RELATION_NAME = ?", (name,))
                cols = [r[0] for r in _rows(conn, "SELECT TRIM(RDB$FIELD_NAME) FROM RDB$RELATION_FIELDS "
                                                  "WHERE RDB$RELATION_NAME = ? ORDER BY RDB$FIELD_POSITION", (name,))]
                return (f"CREATE OR ALTER VIEW {self.quote(name)} (\n    "
                        + ",\n    ".join(self.quote(c) for c in cols) + ")\nAS\n" + _text(src[0][0]).strip() + ";")
            if category == "Procedures":
                src = _rows(conn, "SELECT RDB$PROCEDURE_SOURCE FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME = ?", (name,))
                params = _rows(conn, """
                    SELECT TRIM(p.RDB$PARAMETER_NAME), p.RDB$PARAMETER_TYPE, f.RDB$FIELD_TYPE, f.RDB$FIELD_SUB_TYPE,
                           f.RDB$FIELD_LENGTH, f.RDB$CHARACTER_LENGTH, f.RDB$FIELD_PRECISION, f.RDB$FIELD_SCALE
                      FROM RDB$PROCEDURE_PARAMETERS p JOIN RDB$FIELDS f ON f.RDB$FIELD_NAME = p.RDB$FIELD_SOURCE
                     WHERE p.RDB$PROCEDURE_NAME = ? ORDER BY p.RDB$PARAMETER_TYPE, p.RDB$PARAMETER_NUMBER""", (name,))
                ins = [f"    {self.quote(r[0])} {self._type(*r[2:])}" for r in params if r[1] == 0]
                outs = [f"    {self.quote(r[0])} {self._type(*r[2:])}" for r in params if r[1] == 1]
                head = f"CREATE OR ALTER PROCEDURE {self.quote(name)}"
                if ins:
                    head += " (\n" + ",\n".join(ins) + ")"
                if outs:
                    head += "\nRETURNS (\n" + ",\n".join(outs) + ")"
                return f"SET TERM ^ ;\n\n{head}\nAS\n{_text(src[0][0]).strip()}^\n\nSET TERM ; ^"
            if category == "Triggers":
                r = _rows(conn, "SELECT RDB$TRIGGER_SOURCE, TRIM(RDB$RELATION_NAME), RDB$TRIGGER_TYPE, "
                                "RDB$TRIGGER_SEQUENCE, RDB$TRIGGER_INACTIVE FROM RDB$TRIGGERS WHERE RDB$TRIGGER_NAME = ?",
                          (name,))[0]
                src, rel, ttype, seq, inactive = r
                when = _trigger_type(ttype)
                target = f" FOR {self.quote(rel)}" if rel else ""
                state = "INACTIVE" if inactive else "ACTIVE"
                return (f"SET TERM ^ ;\n\nCREATE OR ALTER TRIGGER {self.quote(name)}{target}\n"
                        f"{state} {when} POSITION {seq or 0}\n{_text(src).strip()}^\n\nSET TERM ; ^")
            if category == "Tabelas":
                return self.table_ddl(conn, name)
            if category == "Generators":
                v = _rows(conn, f"SELECT GEN_ID({self.quote(name)}, 0) FROM RDB$DATABASE")[0][0]
                return f"CREATE SEQUENCE {self.quote(name)};\nALTER SEQUENCE {self.quote(name)} RESTART WITH {v};"
            if category == "Funções":
                src = _rows(conn, "SELECT RDB$FUNCTION_SOURCE FROM RDB$FUNCTIONS WHERE RDB$FUNCTION_NAME = ?", (name,))
                return _text(src[0][0]) if src else None
        finally:
            conn.commit()
        return None


def _trigger_type(t):
    t = t or 0
    if t >= 8192:
        return {8192: "ON CONNECT", 8193: "ON DISCONNECT", 8194: "ON TRANSACTION START",
                8195: "ON TRANSACTION COMMIT", 8196: "ON TRANSACTION ROLLBACK"}.get(t, f"/* type {t} */")
    prefix = "BEFORE" if t % 2 == 1 else "AFTER"
    events = {1: "INSERT", 2: "UPDATE", 3: "DELETE"}
    v = (t + 1) >> 1
    parts = []
    while v:
        e = v & 3
        if e:
            parts.append(events[e])
        v >>= 2
    return f"{prefix} {' OR '.join(parts)}"


def _text(v):
    if v is None:
        return ""
    if hasattr(v, "read"):
        v = v.read()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


# --------------------------------------------------------------------------
class PostgresDriver(Driver):
    fold = "lower"

    key = "postgres"
    label = "PostgreSQL"
    default_port = "5432"
    categories = ["Tabelas", "Views", "Funções", "Sequences"]

    def connect(self, p, password):
        import psycopg

        kw = dict(host=p.host or None, port=p.port or None, dbname=p.database, user=p.user or None,
                  password=password or None)
        kw.update(parse_options(p.options))
        conn = psycopg.connect(**{k: v for k, v in kw.items() if v is not None})
        if p.charset:
            conn.execute(f"SET client_encoding TO '{p.charset}'")
            conn.commit()
        return conn

    def objects(self, conn):
        excl = "('pg_catalog', 'information_schema', 'pg_toast')"
        q = {
            "Tabelas": f"SELECT table_schema||'.'||table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN {excl} ORDER BY 1",
            "Views": f"SELECT table_schema||'.'||table_name FROM information_schema.views WHERE table_schema NOT IN {excl} ORDER BY 1",
            "Funções": f"SELECT DISTINCT n.nspname||'.'||p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN {excl} ORDER BY 1",
            "Sequences": f"SELECT sequence_schema||'.'||sequence_name FROM information_schema.sequences WHERE sequence_schema NOT IN {excl} ORDER BY 1",
        }
        out = {c: [r[0] for r in _rows(conn, s)] for c, s in q.items()}
        conn.commit()
        return out

    def columns(self, conn, table):
        schema, _, name = table.rpartition(".")
        schema = schema or "public"
        rows = _rows(conn, """
            SELECT c.column_name, format_type(a.atttypid, a.atttypmod), c.is_nullable = 'YES', c.column_default,
                   EXISTS (SELECT 1 FROM information_schema.table_constraints tc
                             JOIN information_schema.key_column_usage k
                               ON k.constraint_name = tc.constraint_name AND k.table_schema = tc.table_schema
                            WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = c.table_schema
                              AND tc.table_name = c.table_name AND k.column_name = c.column_name)
              FROM information_schema.columns c
              JOIN pg_attribute a ON a.attrelid = (quote_ident(c.table_schema)||'.'||quote_ident(c.table_name))::regclass
                                 AND a.attname = c.column_name
             WHERE c.table_schema = %s AND c.table_name = %s ORDER BY c.ordinal_position""", (schema, name))
        conn.commit()
        return [dict(name=r[0], type=r[1], nullable=r[2], default=r[3] or "", pk=r[4]) for r in rows]

    def source(self, conn, category, name):
        try:
            if category == "Views":
                r = _rows(conn, "SELECT pg_get_viewdef(%s::regclass, true)", (name,))
                return f"CREATE OR REPLACE VIEW {self.qualified(name)} AS\n{r[0][0]}"
            if category == "Funções":
                schema, _, fn = name.rpartition(".")
                r = _rows(conn, "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n "
                                "ON n.oid=p.pronamespace WHERE n.nspname=%s AND p.proname=%s", (schema, fn))
                return ";\n\n".join(x[0] for x in r) + ";"
            if category == "Tabelas":
                return self.table_ddl(conn, name)
        finally:
            conn.commit()
        return None


class MySQLDriver(Driver):
    def table_exists_sql(self, table):
        schema, name = self._split(table)
        return self._exists(f"information_schema.tables\n WHERE table_schema = {self._lit(schema) if schema else 'DATABASE()'}"
                            f"\n   AND table_name = {self._lit(name)}")

    def column_exists_sql(self, table, column):
        schema, name = self._split(table)
        return self._exists(f"information_schema.columns\n WHERE table_schema = {self._lit(schema) if schema else 'DATABASE()'}"
                            f"\n   AND table_name = {self._lit(name)}"
                            f"\n   AND column_name = {self._lit(self._ident(column))}")

    key = "mysql"
    label = "MySQL / MariaDB"
    default_port = "3306"
    categories = ["Tabelas", "Views", "Procedures", "Funções", "Triggers"]
    block_prefixes = ("CREATE PROCEDURE", "CREATE FUNCTION", "CREATE TRIGGER", "CREATE EVENT",
                      "CREATE DEFINER")

    def connect(self, p, password):
        import pymysql

        kw = dict(host=p.host or "localhost", port=int(p.port or 3306), user=p.user, password=password,
                  database=p.database or None, charset=(p.charset or "utf8mb4").lower(), autocommit=False)
        kw.update(parse_options(p.options))
        return pymysql.connect(**kw)

    def quote(self, name):
        return "`%s`" % name.replace("`", "``")

    def objects(self, conn):
        q = {
            "Tabelas": "SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE' ORDER BY 1",
            "Views": "SELECT table_name FROM information_schema.views WHERE table_schema=DATABASE() ORDER BY 1",
            "Procedures": "SELECT routine_name FROM information_schema.routines WHERE routine_schema=DATABASE() AND routine_type='PROCEDURE' ORDER BY 1",
            "Funções": "SELECT routine_name FROM information_schema.routines WHERE routine_schema=DATABASE() AND routine_type='FUNCTION' ORDER BY 1",
            "Triggers": "SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema=DATABASE() ORDER BY 1",
        }
        out = {c: [r[0] for r in _rows(conn, s)] for c, s in q.items()}
        conn.commit()
        return out

    def columns(self, conn, table):
        rows = _rows(conn, "SELECT column_name, column_type, is_nullable='YES', column_default, column_key='PRI' "
                           "FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s "
                           "ORDER BY ordinal_position", (table,))
        conn.commit()
        return [dict(name=r[0], type=r[1], nullable=bool(r[2]), default="" if r[3] is None else str(r[3]),
                     pk=bool(r[4])) for r in rows]

    def source(self, conn, category, name):
        kind = {"Tabelas": "TABLE", "Views": "VIEW", "Procedures": "PROCEDURE", "Funções": "FUNCTION",
                "Triggers": "TRIGGER"}.get(category)
        if not kind:
            return None
        r = _rows(conn, f"SHOW CREATE {kind} {self.quote(name)}")
        conn.commit()
        row = r[0]
        body = row[2] if kind in ("PROCEDURE", "FUNCTION", "TRIGGER") else row[1]
        if kind in ("PROCEDURE", "FUNCTION", "TRIGGER"):
            return f"DELIMITER $$\n{body}$$\nDELIMITER ;"
        return body + ";"


class SQLServerDriver(Driver):
    key = "mssql"
    label = "SQL Server"
    default_port = "1433"
    categories = ["Tabelas", "Views", "Procedures", "Funções"]

    def connect(self, p, password):
        import pymssql

        kw = dict(server=p.host, port=p.port or "1433", user=p.user, password=password, database=p.database,
                  charset=p.charset or "UTF-8", autocommit=False)
        kw.update(parse_options(p.options))
        return pymssql.connect(**kw)

    def quote(self, name):
        return "[%s]" % name.replace("]", "]]")

    def top_sql(self, table, n):
        return f"SELECT TOP {n} * FROM {self.qualified(table)}"

    def objects(self, conn):
        q = {
            "Tabelas": "SELECT TABLE_SCHEMA+'.'+TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' ORDER BY 1",
            "Views": "SELECT TABLE_SCHEMA+'.'+TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS ORDER BY 1",
            "Procedures": "SELECT ROUTINE_SCHEMA+'.'+ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_TYPE='PROCEDURE' ORDER BY 1",
            "Funções": "SELECT ROUTINE_SCHEMA+'.'+ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_TYPE='FUNCTION' ORDER BY 1",
        }
        out = {c: [r[0] for r in _rows(conn, s)] for c, s in q.items()}
        conn.commit()
        return out

    def columns(self, conn, table):
        schema, _, name = table.rpartition(".")
        rows = _rows(conn, """
            SELECT c.COLUMN_NAME,
                   c.DATA_TYPE + CASE WHEN c.CHARACTER_MAXIMUM_LENGTH = -1 THEN '(max)'
                                      WHEN c.CHARACTER_MAXIMUM_LENGTH IS NOT NULL THEN '(' + CAST(c.CHARACTER_MAXIMUM_LENGTH AS varchar) + ')'
                                      WHEN c.DATA_TYPE IN ('decimal','numeric') THEN '(' + CAST(c.NUMERIC_PRECISION AS varchar) + ',' + CAST(c.NUMERIC_SCALE AS varchar) + ')'
                                      ELSE '' END,
                   CASE WHEN c.IS_NULLABLE = 'YES' THEN 1 ELSE 0 END, c.COLUMN_DEFAULT,
                   CASE WHEN EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                                       JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k ON k.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                                      WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY' AND tc.TABLE_SCHEMA = c.TABLE_SCHEMA
                                        AND tc.TABLE_NAME = c.TABLE_NAME AND k.COLUMN_NAME = c.COLUMN_NAME) THEN 1 ELSE 0 END
              FROM INFORMATION_SCHEMA.COLUMNS c
             WHERE c.TABLE_SCHEMA = %s AND c.TABLE_NAME = %s ORDER BY c.ORDINAL_POSITION""", (schema or "dbo", name))
        conn.commit()
        return [dict(name=r[0], type=r[1], nullable=bool(r[2]), default=r[3] or "", pk=bool(r[4])) for r in rows]

    def source(self, conn, category, name):
        if category == "Tabelas":
            return self.table_ddl(conn, name)
        r = _rows(conn, "SELECT OBJECT_DEFINITION(OBJECT_ID(%s))", (name,))
        conn.commit()
        return r[0][0] if r else None


class OracleDriver(Driver):
    fold = "upper"

    def table_exists_sql(self, table):
        schema, name = self._split(table)
        if schema:
            return self._exists(f"all_tables\n WHERE owner = {self._lit(schema)}\n   AND table_name = {self._lit(name)}")
        return self._exists(f"user_tables\n WHERE table_name = {self._lit(name)}")

    def column_exists_sql(self, table, column):
        schema, name = self._split(table)
        col = self._lit(self._ident(column))
        if schema:
            return self._exists(f"all_tab_columns\n WHERE owner = {self._lit(schema)}"
                                f"\n   AND table_name = {self._lit(name)}\n   AND column_name = {col}")
        return self._exists(f"user_tab_columns\n WHERE table_name = {self._lit(name)}\n   AND column_name = {col}")

    key = "oracle"
    label = "Oracle"
    default_port = "1521"
    database_label = "Service name"
    categories = ["Tabelas", "Views", "Procedures", "Funções", "Packages", "Triggers", "Sequences"]
    block_prefixes = ("BEGIN", "DECLARE", "CREATE PROCEDURE", "CREATE OR REPLACE PROCEDURE", "CREATE FUNCTION",
                      "CREATE OR REPLACE FUNCTION", "CREATE PACKAGE", "CREATE OR REPLACE PACKAGE",
                      "CREATE TRIGGER", "CREATE OR REPLACE TRIGGER", "CREATE TYPE", "CREATE OR REPLACE TYPE")

    def connect(self, p, password):
        import oracledb

        dsn = f"{p.host}:{p.port or 1521}/{p.database}" if p.host else p.database
        return oracledb.connect(user=p.user, password=password, dsn=dsn, **parse_options(p.options))

    def quote(self, name):
        return name if _SIMPLE_UPPER.match(name) else '"%s"' % name.replace('"', '""')

    def top_sql(self, table, n):
        return f"SELECT * FROM {self.qualified(table)} FETCH FIRST {n} ROWS ONLY"

    def objects(self, conn):
        q = {
            "Tabelas": "SELECT table_name FROM user_tables ORDER BY 1",
            "Views": "SELECT view_name FROM user_views ORDER BY 1",
            "Procedures": "SELECT object_name FROM user_objects WHERE object_type='PROCEDURE' ORDER BY 1",
            "Funções": "SELECT object_name FROM user_objects WHERE object_type='FUNCTION' ORDER BY 1",
            "Packages": "SELECT object_name FROM user_objects WHERE object_type='PACKAGE' ORDER BY 1",
            "Triggers": "SELECT trigger_name FROM user_triggers ORDER BY 1",
            "Sequences": "SELECT sequence_name FROM user_sequences ORDER BY 1",
        }
        return {c: [r[0] for r in _rows(conn, s)] for c, s in q.items()}

    def columns(self, conn, table):
        rows = _rows(conn, """
            SELECT c.column_name,
                   c.data_type || CASE WHEN c.data_type IN ('VARCHAR2','CHAR','NVARCHAR2','NCHAR') THEN '('||c.char_length||')'
                                       WHEN c.data_type = 'NUMBER' AND c.data_precision IS NOT NULL THEN '('||c.data_precision||','||c.data_scale||')'
                                       ELSE '' END,
                   CASE WHEN c.nullable = 'Y' THEN 1 ELSE 0 END, c.data_default,
                   (SELECT COUNT(*) FROM user_constraints k JOIN user_cons_columns cc ON cc.constraint_name = k.constraint_name
                     WHERE k.table_name = c.table_name AND k.constraint_type = 'P' AND cc.column_name = c.column_name)
              FROM user_tab_columns c WHERE c.table_name = :1 ORDER BY c.column_id""", (table,))
        return [dict(name=r[0], type=r[1], nullable=bool(r[2]), default=(r[3] or "").strip(), pk=bool(r[4]))
                for r in rows]

    def source(self, conn, category, name):
        kind = {"Tabelas": "TABLE", "Views": "VIEW", "Procedures": "PROCEDURE", "Funções": "FUNCTION",
                "Packages": "PACKAGE", "Triggers": "TRIGGER", "Sequences": "SEQUENCE"}.get(category)
        if not kind:
            return None
        r = _rows(conn, "SELECT DBMS_METADATA.GET_DDL(:1, :2) FROM dual", (kind, name))
        return _text(r[0][0]) if r else None


class SQLiteDriver(Driver):
    def table_exists_sql(self, table):
        name = self._split(table)[1]
        return self._exists(f"sqlite_master\n WHERE type IN ('table', 'view')\n   AND name = {self._lit(name)} COLLATE NOCASE")

    def column_exists_sql(self, table, column):
        name = self._split(table)[1]
        return self._exists(f"pragma_table_info({self._lit(name)})\n WHERE name = {self._lit(self._ident(column))} COLLATE NOCASE")

    key = "sqlite"
    label = "SQLite"
    needs_host = False
    database_label = "Arquivo"
    categories = ["Tabelas", "Views", "Triggers", "Índices"]
    block_prefixes = ("CREATE TRIGGER",)

    def connect(self, p, password):
        conn = sqlite3.connect(p.database, check_same_thread=False, isolation_level="DEFERRED")
        return conn

    def objects(self, conn):
        kinds = {"Tabelas": "table", "Views": "view", "Triggers": "trigger", "Índices": "index"}
        return {c: [r[0] for r in _rows(conn, "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE "
                                              "'sqlite_%' ORDER BY 1", (k,))] for c, k in kinds.items()}

    def columns(self, conn, table):
        rows = _rows(conn, f"PRAGMA table_info({self.quote(table)})")
        return [dict(name=r[1], type=r[2], nullable=not r[3], default=r[4] or "", pk=bool(r[5])) for r in rows]

    def source(self, conn, category, name):
        r = _rows(conn, "SELECT sql FROM sqlite_master WHERE name=?", (name,))
        return (r[0][0] + ";") if r and r[0][0] else None


DRIVERS: dict[str, Driver] = {d.key: d for d in (FirebirdDriver(), PostgresDriver(), MySQLDriver(),
                                                 SQLServerDriver(), OracleDriver(), SQLiteDriver())}


def get_driver(key: str) -> Driver:
    return DRIVERS[key]
