#!/usr/bin/env python3
"""Typed GitHub write operations and raw API migration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re

import click

_REVIEW_REPLY_ENDPOINT = re.compile(
    r"^/?repos/([^/]+)/([^/]+)/pulls/([1-9][0-9]*)/comments/([1-9][0-9]*)/replies(?:\?.*)?$"
)
_ADD_SUB_ISSUE_ENDPOINT = re.compile(
    r"^/?repos/([^/]+)/([^/]+)/issues/([1-9][0-9]*)/sub_issues(?:\?.*)?$"
)
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_VALUE_FLAGS = ("--repo", "--number", "--comment-id", "--body", "--body-file")
_ADD_SUB_ISSUE_FLAGS = ("--repo", "--parent", "--sub-issue-id")


@dataclass(frozen=True)
class ReviewReplyOperation:
    repository: str
    number: int
    comment_id: int
    body: str | None = None
    body_file: str | None = None

    operation_name = "pr.review-reply"

    @property
    def endpoint(self) -> str:
        return (
            f"repos/{self.repository}/pulls/{self.number}/comments/"
            f"{self.comment_id}/replies"
        )

    def typed_args(self) -> list[str]:
        args = [
            "pr",
            "review-reply",
            "--repo",
            self.repository,
            "--number",
            str(self.number),
            "--comment-id",
            str(self.comment_id),
        ]
        if self.body is not None:
            args.extend(["--body", self.body])
        elif self.body_file is not None:
            args.extend(["--body-file", self.body_file])
        return args

    def execution_args(self) -> list[str]:
        args = ["gh", "api", "-X", "POST", self.endpoint]
        if self.body is not None:
            args.extend(["-f", f"body={self.body}"])
        else:
            args.extend(["-F", f"body=@{self.body_file}"])
        return args


@dataclass(frozen=True)
class AddSubIssueOperation:
    repository: str
    parent: int
    sub_issue_id: int | str

    operation_name = "issue.add-sub-issue"

    @property
    def endpoint(self) -> str:
        return f"repos/{self.repository}/issues/{self.parent}/sub_issues"

    def typed_args(self) -> list[str]:
        return [
            "issue",
            "add-sub-issue",
            "--repo",
            self.repository,
            "--parent",
            str(self.parent),
            "--sub-issue-id",
            str(self.sub_issue_id),
        ]

    def execution_args(self) -> list[str]:
        return [
            "gh",
            "api",
            "--method",
            "POST",
            self.endpoint,
            "-F",
            f"sub_issue_id={self.sub_issue_id}",
        ]


def parse_review_reply(args: list[str]) -> ReviewReplyOperation | None:
    """Parse ``pr review-reply`` or return None for another operation."""
    if args[:2] != ["pr", "review-reply"]:
        return None

    values: dict[str, str] = {}
    index = 2
    while index < len(args):
        arg = args[index]
        if arg == "--reason":
            if index + 1 >= len(args):
                raise click.ClickException("--reason requires a value")
            index += 2
            continue
        if arg.startswith("--reason="):
            index += 1
            continue

        flag = None
        value = None
        for candidate in _VALUE_FLAGS:
            if arg == candidate:
                flag = candidate
                if index + 1 >= len(args):
                    raise click.ClickException(f"{candidate} requires a value")
                value = args[index + 1]
                index += 2
                break
            if arg.startswith(candidate + "="):
                flag = candidate
                value = arg.split("=", 1)[1]
                index += 1
                break
        if flag is None:
            raise click.ClickException(
                f"unsupported pr review-reply argument: {arg}"
            )
        if flag in values:
            raise click.ClickException(f"{flag} must be specified once")
        values[flag] = value or ""

    missing = [
        flag
        for flag in ("--repo", "--number", "--comment-id")
        if not values.get(flag)
    ]
    if missing:
        raise click.ClickException(
            "pr review-reply requires " + ", ".join(missing)
        )
    if not _REPOSITORY.fullmatch(values["--repo"]):
        raise click.ClickException("--repo must use OWNER/REPOSITORY format")

    number = _positive_integer(values["--number"], "--number")
    comment_id = _positive_integer(values["--comment-id"], "--comment-id")
    body = values.get("--body")
    body_file = values.get("--body-file")
    if bool(body) == bool(body_file):
        raise click.ClickException(
            "pr review-reply requires exactly one of --body or --body-file"
        )
    if body_file and not os.path.isfile(body_file):
        raise click.ClickException(f"--body-file is not a file: {body_file}")

    return ReviewReplyOperation(
        repository=values["--repo"],
        number=number,
        comment_id=comment_id,
        body=body,
        body_file=body_file,
    )


def parse_add_sub_issue(args: list[str]) -> AddSubIssueOperation | None:
    """Parse ``issue add-sub-issue`` or return None for another operation."""
    if args[:2] != ["issue", "add-sub-issue"]:
        return None

    values: dict[str, str] = {}
    index = 2
    while index < len(args):
        arg = args[index]
        if arg == "--reason":
            if index + 1 >= len(args):
                raise click.ClickException("--reason requires a value")
            index += 2
            continue
        if arg.startswith("--reason="):
            index += 1
            continue

        flag = None
        value = None
        for candidate in _ADD_SUB_ISSUE_FLAGS:
            if arg == candidate:
                flag = candidate
                if index + 1 >= len(args):
                    raise click.ClickException(f"{candidate} requires a value")
                value = args[index + 1]
                index += 2
                break
            if arg.startswith(candidate + "="):
                flag = candidate
                value = arg.split("=", 1)[1]
                index += 1
                break
        if flag is None:
            raise click.ClickException(
                f"unsupported issue add-sub-issue argument: {arg}"
            )
        if flag in values:
            raise click.ClickException(f"{flag} must be specified once")
        values[flag] = value or ""

    missing = [flag for flag in _ADD_SUB_ISSUE_FLAGS if not values.get(flag)]
    if missing:
        raise click.ClickException(
            "issue add-sub-issue requires " + ", ".join(missing)
        )
    if not _REPOSITORY.fullmatch(values["--repo"]):
        raise click.ClickException("--repo must use OWNER/REPOSITORY format")
    return AddSubIssueOperation(
        repository=values["--repo"],
        parent=_positive_integer(values["--parent"], "--parent"),
        sub_issue_id=_positive_integer(
            values["--sub-issue-id"],
            "--sub-issue-id",
        ),
    )


def parse_typed_operation(
    args: list[str],
) -> ReviewReplyOperation | AddSubIssueOperation | None:
    return parse_review_reply(args) or parse_add_sub_issue(args)


def match_raw_review_reply(args: list[str]) -> ReviewReplyOperation | None:
    """Recognize a raw REST review-reply POST in any normal gh API shape."""
    if args[:2] != ["gh", "api"]:
        return None

    methods = _raw_methods(args[2:])
    if len(methods) > 1:
        return None
    method = methods[0] if methods else "POST" if _has_request_data(args[2:]) else "GET"
    if method != "POST":
        return None

    endpoints = [arg for arg in args[2:] if _REVIEW_REPLY_ENDPOINT.fullmatch(arg)]
    if len(endpoints) != 1:
        return None
    match = _REVIEW_REPLY_ENDPOINT.fullmatch(endpoints[0])
    if match is None:
        return None

    body = _raw_body_field(args)
    return ReviewReplyOperation(
        repository=f"{match.group(1)}/{match.group(2)}",
        number=int(match.group(3)),
        comment_id=int(match.group(4)),
        body=body or "<reply-body>",
    )


def match_raw_add_sub_issue(args: list[str]) -> AddSubIssueOperation | None:
    """Recognize a raw REST add-sub-issue POST."""
    if args[:2] != ["gh", "api"]:
        return None
    methods = _raw_methods(args[2:])
    if len(methods) > 1:
        return None
    method = methods[0] if methods else "POST" if _has_request_data(args[2:]) else "GET"
    if method != "POST":
        return None
    endpoints = [arg for arg in args[2:] if _ADD_SUB_ISSUE_ENDPOINT.fullmatch(arg)]
    if len(endpoints) != 1:
        return None
    match = _ADD_SUB_ISSUE_ENDPOINT.fullmatch(endpoints[0])
    if match is None:
        return None
    sub_issue_id = _raw_integer_field(args, "sub_issue_id")
    return AddSubIssueOperation(
        repository=f"{match.group(1)}/{match.group(2)}",
        parent=int(match.group(3)),
        sub_issue_id=sub_issue_id or "<sub-issue-id>",
    )


def match_supported_raw_write(
    args: list[str],
) -> ReviewReplyOperation | AddSubIssueOperation | None:
    return match_raw_review_reply(args) or match_raw_add_sub_issue(args)


def github_operation_execution_args(args: list[str]) -> list[str] | None:
    """Translate a full typed ``gh`` argv into a fixed REST request."""
    if not args or args[0] != "gh":
        return None
    operation = parse_typed_operation(args[1:])
    return operation.execution_args() if operation is not None else None


def review_reply_execution_args(args: list[str]) -> list[str] | None:
    """Translate a full typed ``gh`` argv into the fixed REST request."""
    if not args or args[0] != "gh":
        return None
    operation = parse_review_reply(args[1:])
    return operation.execution_args() if operation is not None else None


def _positive_integer(value: str, flag: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise click.ClickException(f"{flag} must be a positive integer") from exc
    if parsed <= 0:
        raise click.ClickException(f"{flag} must be a positive integer")
    return parsed


def _raw_methods(args: list[str]) -> list[str]:
    methods = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-X", "--method"):
            if index + 1 < len(args):
                methods.append(args[index + 1].upper())
            index += 2
            continue
        if arg.startswith("--method="):
            methods.append(arg.split("=", 1)[1].upper())
        elif arg.startswith("-X") and len(arg) > 2:
            methods.append(arg[2:].upper())
        index += 1
    return methods


def _has_request_data(args: list[str]) -> bool:
    return any(
        arg in ("-f", "--raw-field", "-F", "--field", "--input")
        or arg.startswith(("-f", "-F", "--raw-field=", "--field=", "--input="))
        for arg in args
    )


def _raw_integer_field(args: list[str], name: str) -> int | None:
    index = 0
    while index < len(args):
        arg = args[index]
        value = None
        if arg in ("-f", "--raw-field", "-F", "--field"):
            if index + 1 < len(args):
                value = args[index + 1]
            index += 2
        elif arg.startswith(("--raw-field=", "--field=")):
            value = arg.split("=", 1)[1]
            index += 1
        elif (arg.startswith("-f") or arg.startswith("-F")) and len(arg) > 2:
            value = arg[2:]
            index += 1
        else:
            index += 1
        if value is None or not value.startswith(name + "="):
            continue
        raw = value.split("=", 1)[1]
        if re.fullmatch(r"[1-9][0-9]*", raw):
            return int(raw)
    return None


def _raw_body_field(args: list[str]) -> str | None:
    index = 0
    while index < len(args):
        arg = args[index]
        value = None
        if arg in ("-f", "--raw-field", "-F", "--field"):
            if index + 1 < len(args):
                value = args[index + 1]
            index += 2
        elif arg.startswith(("--raw-field=", "--field=")):
            value = arg.split("=", 1)[1]
            index += 1
        elif (arg.startswith("-f") or arg.startswith("-F")) and len(arg) > 2:
            value = arg[2:]
            index += 1
        else:
            index += 1
        if value is not None and value.startswith("body="):
            body = value.split("=", 1)[1]
            if body and not body.startswith("@"):
                return body
    return None
