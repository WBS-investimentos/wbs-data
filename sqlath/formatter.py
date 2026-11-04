"""Formatação de SQL (Ctrl+Shift+F).

Formata comando a comando com o sqlparse. Blocos PSQL/PL-SQL (procedures,
triggers, EXECUTE BLOCK...) e diretivas como SET TERM ficam intactos: o
sqlparse não entende esses corpos e quebraria a indentação deles.
"""
from __future__ import annotations

import re

import sqlparse

from .sqlsplit import leading_words, split_sql

# strings e identificadores entre aspas: nunca são tocados
_QUOTED = re.compile(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")")
_FIREBIRD_KEYWORDS = re.compile(
    r"\b(first|skip|containing|starting|rows|returning|matching|similar|list|iif|gen_id|dateadd|datediff)\b", re.I)
_IN_LIST = re.compile(r"\bIN \(([^()']*)\)", re.I)


def _outside_quotes(text: str, fn) -> str:
    parts = _QUOTED.split(text)
    return "".join(fn(p) if i % 2 == 0 else p for i, p in enumerate(parts))


def _tidy(sql: str, dialect: str) -> str:
    def fix(part: str) -> str:
        part = re.sub(r"\n[ \t]*\n", "\n", part)                       # linha em branco antes de subselect
        part = _IN_LIST.sub(lambda m: "IN (" + re.sub(r",\s+", ", ", m.group(1)) + ")", part)
        if dialect == "firebird":
            part = _FIREBIRD_KEYWORDS.sub(lambda m: m.group(1).upper(), part)
        return part

    return _outside_quotes(sql, fix)


def format_statement(sql: str, dialect: str = "firebird") -> str:
    out = sqlparse.format(sql, reindent=True, keyword_case="upper", indent_width=4,
                          use_space_around_operators=True, strip_whitespace=False)
    return _tidy(out.strip(), dialect)


def format_sql(text: str, dialect: str = "firebird", block_prefixes: tuple[str, ...] = ()) -> tuple[str, int, int]:
    """Devolve (texto formatado, comandos formatados, blocos mantidos)."""
    prefixes = [p.split() for p in block_prefixes]
    out = []
    pos = 0
    formatted = kept = 0
    for st in split_sql(text, dialect, block_prefixes):
        out.append(text[pos:st.start])          # terminadores, SET TERM, espaços: como estavam
        chunk = text[st.start:st.end]
        words = leading_words(chunk, 4)
        if any(words[:len(p)] == p for p in prefixes):
            out.append(chunk)
            kept += 1
        else:
            out.append(format_statement(chunk, dialect))
            formatted += 1
        pos = st.end
    out.append(text[pos:])
    return "".join(out), formatted, kept
