#!/usr/bin/env python3
"""Conservative classifier for read-only GitHub API commands."""

from __future__ import annotations

from dataclasses import dataclass

from ozm.github_graphql import read_only_reason as graphql_read_only_reason

REST_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
_METHOD_FLAGS = frozenset({"-X", "--method"})
_FIELD_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field"})
_VALUE_FLAGS = frozenset({
    "-H",
    "-p",
    "-q",
    "-t",
    "--cache",
    "--header",
    "--hostname",
    "--jq",
    "--preview",
    "--template",
})
_BOOLEAN_FLAGS = frozenset({
    "-i",
    "--include",
    "--paginate",
    "--silent",
    "--slurp",
    "--verbose",
})
_UNSAFE_HEADER_NAMES = frozenset({
    "x-http-method",
    "x-http-method-override",
    "x-method-override",
})
_HIGH_LEVEL_READ_COMMANDS = frozenset({
    ("auth", "status"),
    ("issue", "list"),
    ("issue", "status"),
    ("issue", "view"),
    ("label", "list"),
    ("pr", "checks"),
    ("pr", "diff"),
    ("pr", "list"),
    ("pr", "status"),
    ("pr", "view"),
    ("project", "item-list"),
    ("project", "list"),
    ("project", "view"),
    ("release", "list"),
    ("release", "view"),
    ("repo", "list"),
    ("repo", "view"),
    ("run", "list"),
    ("run", "view"),
    ("run", "watch"),
    ("search", "code"),
    ("search", "commits"),
    ("search", "issues"),
    ("search", "prs"),
    ("search", "repos"),
    ("workflow", "list"),
    ("workflow", "view"),
})


@dataclass(frozen=True)
class GitHubRESTRequest:
    method: str
    endpoint: str


def read_only_reason(args: list[str]) -> str | None:
    """Return an allow reason for a proven read-only GitHub operation."""
    high_level_reason = high_level_read_only_reason(args)
    if high_level_reason:
        return high_level_reason

    graphql_reason = graphql_read_only_reason(args)
    if graphql_reason:
        return graphql_reason

    request = extract_rest_request(args)
    if request is None or request.method not in REST_READ_ONLY_METHODS:
        return None
    return f"github rest {request.method}"


def high_level_read_only_reason(args: list[str]) -> str | None:
    if len(args) < 3 or args[0] != "gh":
        return None
    command = (args[1], args[2])
    if command not in _HIGH_LEVEL_READ_COMMANDS:
        return None
    return f"github {command[0]} {command[1]}"


def extract_rest_request(args: list[str]) -> GitHubRESTRequest | None:
    """Parse one unambiguous REST request from ``gh api`` argv."""
    if len(args) < 3:
        return None
    if args[0] != "gh" or args[1] != "api":
        return None

    endpoint = None
    explicit_method = None
    saw_field = False
    index = 2

    while index < len(args):
        arg = args[index]

        if arg == "--":
            return None

        if arg in _METHOD_FLAGS:
            value = _next_value(args, index)
            if value is None or explicit_method is not None:
                return None
            explicit_method = value.upper()
            index += 2
            continue
        if arg.startswith("--method="):
            if explicit_method is not None:
                return None
            explicit_method = arg.split("=", 1)[1].upper()
            if not explicit_method:
                return None
            index += 1
            continue
        if arg.startswith("-X") and arg != "-X":
            if explicit_method is not None:
                return None
            explicit_method = arg[2:].upper()
            if not explicit_method:
                return None
            index += 1
            continue

        if arg == "--input" or arg.startswith("--input="):
            return None

        if arg in _FIELD_FLAGS:
            value = _next_value(args, index)
            if value is None or _is_file_backed_field(value):
                return None
            saw_field = True
            index += 2
            continue
        matched_field = _attached_short_value(arg, ("-f", "-F"))
        if matched_field is not None:
            if _is_file_backed_field(matched_field):
                return None
            saw_field = True
            index += 1
            continue
        if arg.startswith("--field=") or arg.startswith("--raw-field="):
            value = arg.split("=", 1)[1]
            if not value or _is_file_backed_field(value):
                return None
            saw_field = True
            index += 1
            continue

        if arg in _VALUE_FLAGS:
            value = _next_value(args, index)
            if value is None:
                return None
            if arg in {"-H", "--header"} and _unsafe_header(value):
                return None
            index += 2
            continue
        matched_value = _attached_short_value(arg, ("-H", "-p", "-q", "-t"))
        if matched_value is not None:
            if arg.startswith("-H") and _unsafe_header(matched_value):
                return None
            index += 1
            continue
        if any(arg.startswith(prefix) for prefix in (
            "--cache=",
            "--header=",
            "--hostname=",
            "--jq=",
            "--preview=",
            "--template=",
        )):
            value = arg.split("=", 1)[1]
            if not value:
                return None
            if arg.startswith("--header=") and _unsafe_header(value):
                return None
            index += 1
            continue

        if arg in _BOOLEAN_FLAGS:
            index += 1
            continue
        if arg.startswith("-"):
            return None

        if endpoint is not None:
            return None
        endpoint = arg
        index += 1

    if explicit_method is not None and not _valid_method(explicit_method):
        return None
    if endpoint is None or not _safe_relative_endpoint(endpoint):
        return None
    if endpoint.lstrip("/").split("?", 1)[0] == "graphql":
        return None

    method = explicit_method or ("POST" if saw_field else "GET")
    return GitHubRESTRequest(method=method, endpoint=endpoint)


def _next_value(args: list[str], index: int) -> str | None:
    if index + 1 >= len(args):
        return None
    value = args[index + 1]
    return value if value else None


def _attached_short_value(arg: str, flags: tuple[str, ...]) -> str | None:
    for flag in flags:
        if arg.startswith(flag) and arg != flag:
            return arg[len(flag):]
    return None


def _is_file_backed_field(value: str) -> bool:
    _name, separator, field_value = value.partition("=")
    return bool(separator and field_value.startswith("@"))


def _unsafe_header(value: str) -> bool:
    if value.startswith("@"):
        return True
    name, separator, _header_value = value.partition(":")
    if not separator:
        return True
    return name.strip().lower() in _UNSAFE_HEADER_NAMES


def _valid_method(method: str) -> bool:
    return bool(method) and all(char.isalpha() or char == "-" for char in method)


def _safe_relative_endpoint(endpoint: str) -> bool:
    endpoint = endpoint.strip()
    if not endpoint or "://" in endpoint or any(char.isspace() for char in endpoint):
        return False
    return not endpoint.startswith("-")
