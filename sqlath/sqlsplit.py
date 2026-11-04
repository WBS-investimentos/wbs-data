"""Divide um script SQL em comandos.

Entende strings, identificadores entre aspas, comentarios, dollar-quotes do
PostgreSQL, SET TERM (Firebird), DELIMITER (MySQL), "/" em linha propria
(Oracle) e blocos BEGIN..END de procedures/triggers/EXECUTE BLOCK.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_SET_TERM = re.compile(r"\s*SET\s+TERM\s+(\S+)\s*", re.I)
_DELIMITER = re.compile(r"[ \t]*DELIMITER[ \t]+(\S+)[ \t]*(?:\r?\n|$)", re.I)

READ_ONLY_KEYWORDS = {"SELECT", "WITH", "SHOW", "EXPLAIN", "DESCRIBE", "DESC", "PRAGMA", "VALUES"}


@dataclass
class Statement:
    sql: str
    start: int
    end: int


def strip_leading_comments(sql: str) -> str:
    s = sql
    while True:
        s = s.lstrip()
        if s.startswith("--"):
            nl = s.find("\n")
            s = "" if nl < 0 else s[nl + 1:]
        elif s.startswith("/*"):
            e = s.find("*/")
            s = "" if e < 0 else s[e + 2:]
        else:
            return s


def leading_words(sql: str, n: int = 4) -> list[str]:
    return [w.upper() for w in _WORD.findall(strip_leading_comments(sql)[:300])[:n]]


def is_read_only(sql: str) -> bool:
    w = leading_words(sql, 1)
    return bool(w) and w[0] in READ_ONLY_KEYWORDS


def _skip(text: str, i: int, dialect: str) -> int | None:
    """Se em text[i] começa string/comentário, devolve o índice após ele."""
    c = text[i]
    n = len(text)
    if c == "-" and text.startswith("--", i):
        e = text.find("\n", i)
        return n if e < 0 else e + 1
    if c == "#" and dialect == "mysql":
        e = text.find("\n", i)
        return n if e < 0 else e + 1
    if c == "/" and text.startswith("/*", i):
        e = text.find("*/", i + 2)
        return n if e < 0 else e + 2
    if c in "'\"`" or (c == "[" and dialect == "mssql"):
        close = "]" if c == "[" else c
        j = i + 1
        while j < n:
            if text[j] == close:
                if j + 1 < n and text[j + 1] == close:
                    j += 2
                    continue
                return j + 1
            if text[j] == "\\" and dialect == "mysql" and c != "`":
                j += 2
                continue
            j += 1
        return n
    if c == "$" and dialect == "postgres":
        m = _DOLLAR.match(text, i)
        if m:
            e = text.find(m.group(0), m.end())
            return n if e < 0 else e + len(m.group(0))
    return None


def split_sql(text: str, dialect: str = "firebird", block_prefixes: tuple[str, ...] = ()) -> list[Statement]:
    out: list[Statement] = []
    term = ";"
    n = len(text)
    i = 0
    start = 0
    prefixes = tuple(p.split() for p in block_prefixes)

    # estado do comando corrente
    block_mode: bool | None = None
    depth = 0
    seen_begin = False
    seen_body = False

    def emit(s: int, e: int):
        chunk = text[s:e]
        if strip_leading_comments(chunk).strip():
            lead = len(chunk) - len(chunk.lstrip())
            out.append(Statement(chunk.strip(), s + lead, s + len(chunk.rstrip())))

    def reset():
        nonlocal block_mode, depth, seen_begin, seen_body
        block_mode, depth, seen_begin, seen_body = None, 0, False, False

    while i < n:
        # diretivas no início de um comando
        if block_mode is None and not text[start:i].strip():
            m = _SET_TERM.match(text, i)
            if m and dialect == "firebird":
                new_term, pos = m.group(1), m.end()
                if new_term != term and new_term.endswith(term):   # "SET TERM ^;"
                    new_term, pos = new_term[:-len(term)], m.start(1) + len(new_term)
                elif text.startswith(term, pos):
                    pos += len(term)
                else:
                    new_term = None
                if new_term:
                    term = new_term
                    i = start = pos
                    continue
            m = _DELIMITER.match(text, i)
            if m and dialect == "mysql":
                term = m.group(1)
                i = start = m.end()
                continue

        sk = _skip(text, i, dialect)
        if sk is not None:
            i = sk
            continue

        c = text[i]

        # Oracle: "/" sozinho em uma linha encerra o bloco
        if dialect == "oracle" and c == "/" and (i == 0 or text[i - 1] == "\n"):
            line_end = text.find("\n", i)
            line_end = n if line_end < 0 else line_end
            if not text[i + 1:line_end].strip():
                emit(start, i)
                i = start = line_end
                reset()
                continue

        if c.isalpha() or c == "_":
            m = _WORD.match(text, i)
            k = m.group(0).find(term) if term != ";" else -1
            if k > 0:  # terminador colado no identificador, ex.: END$$
                m = _WORD.match(text[:i + k], i)
            word = m.group(0).upper()
            if block_mode is None:
                words = leading_words(text[start:start + 400], 4)
                block_mode = term == ";" and any(words[:len(p)] == p for p in prefixes)
            if block_mode:
                if word in ("AS", "IS", "DECLARE"):
                    seen_body = True
                elif word == "BEGIN":
                    depth += 1
                    seen_begin = True
                elif word == "CASE":
                    depth += 1
                elif word == "END":
                    nm = _WORD.match(text, _next_nonspace(text, m.end()))
                    nxt = nm.group(0).upper() if nm else ""
                    if nxt in ("IF", "LOOP", "WHILE", "REPEAT", "FOR"):
                        i = nm.end()
                        continue
                    depth = max(0, depth - 1)
                    if nxt == "CASE":
                        i = nm.end()
                        continue
            i = m.end()
            continue

        if text.startswith(term, i):
            if block_mode and term == ";":
                if depth > 0 or (seen_body and not seen_begin):
                    i += 1
                    continue
            # PL/SQL exige o ";" final do bloco
            emit(start, i + 1 if (block_mode and dialect == "oracle") else i)
            i = start = i + len(term)
            reset()
            continue

        i += 1

    emit(start, n)
    return out


def _next_nonspace(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def kind_of(sql: str) -> str:
    """Sub-aba a que o comando pertence: SELECT, INSERT, UPDATE, DELETE ou OUTROS."""
    w = leading_words(sql, 3)
    if not w:
        return "OUTROS"
    if w[0] in ("SELECT", "WITH"):
        return "SELECT"
    if w[0] in ("INSERT", "MERGE", "REPLACE") or w[:3] == ["UPDATE", "OR", "INSERT"]:
        return "INSERT"
    if w[0] == "UPDATE":
        return "UPDATE"
    if w[0] == "DELETE":
        return "DELETE"
    return "OUTROS"
