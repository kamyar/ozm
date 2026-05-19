#!/usr/bin/env python3
"""Conservative classifier for read-only GitHub GraphQL commands."""

from __future__ import annotations

from dataclasses import dataclass
import os


READ_ONLY_REASON = "github graphql query"

_FIELD_FLAGS = {"-f", "-F", "--field", "--raw-field"}
_FIELD_PREFIXES = ("--field=", "--raw-field=")
_OPERATION_KINDS = {"query", "mutation", "subscription"}
_PUNCTUATORS = set("!$&():=@[]{}|")


@dataclass(frozen=True)
class _Operation:
    kind: str
    name: str | None


def read_only_reason(args: list[str]) -> str | None:
    """Return an allow reason when argv is definitely a read-only gh GraphQL query."""
    request = _extract_request(args)
    if request is None:
        return None
    query, operation_name = request
    if _selected_operation_kind(query, operation_name) == "query":
        return READ_ONLY_REASON
    return None


def _extract_request(args: list[str]) -> tuple[str, str | None] | None:
    if len(args) < 3:
        return None
    if os.path.basename(args[0]) != "gh" or args[1:3] != ["api", "graphql"]:
        return None

    query = None
    operation_name = None
    i = 3
    while i < len(args):
        arg = args[i]
        field = None
        if arg in _FIELD_FLAGS:
            if i + 1 >= len(args):
                return None
            field = args[i + 1]
            i += 2
        elif any(arg.startswith(prefix) for prefix in _FIELD_PREFIXES):
            field = arg.split("=", 1)[1]
            i += 1
        elif arg == "--input" or arg.startswith("--input="):
            return None
        else:
            i += 1

        if field is None:
            continue
        name, separator, value = field.partition("=")
        if not separator:
            continue
        if name == "query":
            if query is not None or value.startswith("@"):
                return None
            query = value
        elif name == "operationName":
            if operation_name is not None or value.startswith("@"):
                return None
            operation_name = value or None

    if not query:
        return None
    return query, operation_name


def _selected_operation_kind(document: str, operation_name: str | None) -> str | None:
    tokens = _tokenize(document)
    if tokens is None:
        return None
    operations = _parse_operations(tokens)
    if not operations:
        return None
    if len(operations) > 1 and any(operation.name is None for operation in operations):
        return None
    if operation_name:
        matches = [operation for operation in operations if operation.name == operation_name]
        if len(matches) == 1:
            return matches[0].kind
        return None
    if len(operations) == 1:
        return operations[0].kind
    return None


def _parse_operations(tokens: list[str]) -> list[_Operation] | None:
    operations = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "{":
            next_i = _skip_balanced_selection(tokens, i)
            if next_i is None:
                return None
            operations.append(_Operation("query", None))
            i = next_i
            continue
        if token in _OPERATION_KINDS:
            kind = token
            i += 1
            name = None
            if i < len(tokens) and _is_name(tokens[i]):
                name = tokens[i]
                i += 1
            selection_start = _find_selection_start(tokens, i)
            if selection_start is None:
                return None
            next_i = _skip_balanced_selection(tokens, selection_start)
            if next_i is None:
                return None
            operations.append(_Operation(kind, name))
            i = next_i
            continue
        if token == "fragment":
            selection_start = _find_selection_start(tokens, i + 1)
            if selection_start is None:
                return None
            next_i = _skip_balanced_selection(tokens, selection_start)
            if next_i is None:
                return None
            i = next_i
            continue
        return None
    return operations


def _find_selection_start(tokens: list[str], start: int) -> int | None:
    paren_depth = 0
    bracket_depth = 0
    for i in range(start, len(tokens)):
        token = tokens[i]
        if token == "(":
            paren_depth += 1
        elif token == ")":
            paren_depth -= 1
            if paren_depth < 0:
                return None
        elif token == "[":
            bracket_depth += 1
        elif token == "]":
            bracket_depth -= 1
            if bracket_depth < 0:
                return None
        elif token == "{" and paren_depth == 0 and bracket_depth == 0:
            return i
    return None


def _skip_balanced_selection(tokens: list[str], start: int) -> int | None:
    depth = 0
    for i in range(start, len(tokens)):
        token = tokens[i]
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                return i + 1
            if depth < 0:
                return None
    return None


def _tokenize(document: str) -> list[str] | None:
    tokens = []
    i = 0
    while i < len(document):
        char = document[i]
        if char.isspace() or char == ",":
            i += 1
            continue
        if char == "#":
            newline = document.find("\n", i)
            if newline == -1:
                break
            i = newline + 1
            continue
        if document.startswith("...", i):
            tokens.append("...")
            i += 3
            continue
        if document.startswith('"""', i):
            end = document.find('"""', i + 3)
            if end == -1:
                return None
            i = end + 3
            continue
        if char == '"':
            i = _skip_string(document, i)
            if i is None:
                return None
            continue
        if char.isalpha() or char == "_":
            start = i
            i += 1
            while i < len(document) and (document[i].isalnum() or document[i] == "_"):
                i += 1
            tokens.append(document[start:i])
            continue
        if char == "-" or char.isdigit():
            i = _skip_number(document, i)
            if i is None:
                return None
            continue
        if char in _PUNCTUATORS:
            tokens.append(char)
            i += 1
            continue
        return None
    return tokens


def _skip_string(document: str, start: int) -> int | None:
    i = start + 1
    while i < len(document):
        char = document[i]
        if char == "\\":
            i += 2
            continue
        if char == '"':
            return i + 1
        i += 1
    return None


def _skip_number(document: str, start: int) -> int | None:
    i = start
    if document[i] == "-":
        i += 1
    saw_digit = False
    while i < len(document) and document[i].isdigit():
        saw_digit = True
        i += 1
    if i < len(document) and document[i] == ".":
        i += 1
        while i < len(document) and document[i].isdigit():
            saw_digit = True
            i += 1
    if not saw_digit:
        return None
    if i < len(document) and document[i] in {"e", "E"}:
        i += 1
        if i < len(document) and document[i] in {"+", "-"}:
            i += 1
        exponent_start = i
        while i < len(document) and document[i].isdigit():
            i += 1
        if i == exponent_start:
            return None
    return i


def _is_name(token: str) -> bool:
    return bool(token) and (token[0].isalpha() or token[0] == "_")
