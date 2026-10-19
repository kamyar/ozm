"""Conservative shell descriptions for audit output, never authorization.

Only command names leave this parser. It does not execute or expand input.
Unsupported grammar and bounded-input failures produce an unknown description.
"""

import os
import re

_NAME = re.compile(r"[A-Za-z0-9_.+-]+\Z")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_DESCRIPTOR = re.compile(r"[0-9]+\Z")
_VARIABLE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
_CONTROL = {"case", "do", "done", "elif", "else", "esac", "fi", "for", "function", "if", "then", "until", "while", "eval"}


class _UnknownShell(ValueError):
    pass


def _substitution_end(text: str, start: int, depth: int) -> int:
    if depth > 8:
        raise _UnknownShell()
    index = start
    quote = None
    parentheses = 1
    while index < len(text):
        char = text[index]
        if quote == "'":
            if char == "'":
                quote = None
        elif char == "\\":
            index += 1
        elif char == "`" or (quote is None and text.startswith(("$'", '$"'), index)):
            raise _UnknownShell()
        elif char == "#" and quote is None:
            # A commented ')' must not close the substitution. Without a
            # complete comment lexer, do not infer a boundary after '#'.
            raise _UnknownShell()
        elif text.startswith("$(", index):
            index = _substitution_end(text, index + 2, depth + 1)
        elif quote == '"':
            if char == '"':
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            parentheses += 1
        elif char == ")":
            parentheses -= 1
            if parentheses == 0:
                return index
        index += 1
    raise _UnknownShell()


def describe_shell(text: str, *, _depth: int = 0) -> dict:
    """Describe simple commands, pipelines, assignments, and substitutions."""
    try:
        families, nested = _describe(text, _depth)
    except _UnknownShell:
        return {"families": ["unknown"], "nested_families": [], "classification": "unknown"}
    return {
        "families": list(dict.fromkeys([*nested, *families]))[:8],
        "nested_families": list(dict.fromkeys(nested))[:8],
        "classification": "unknown" if "unknown" in families + nested else "static",
    }


def _describe(text: str, depth: int) -> tuple[list[str], list[str]]:
    if depth > 8 or len(text) > 100_000:
        raise _UnknownShell()
    families = []
    nested = []
    words = []
    word = []
    dynamic = False
    started = False
    plain_prefix = True
    assignment_word = False
    assigned = False
    quote = None
    index = 0
    redirection_operand = False
    required_command = False

    def finish_word():
        nonlocal dynamic, redirection_operand, started, plain_prefix, assignment_word
        if word or dynamic or started:
            if not redirection_operand:
                words.append(("".join(word), dynamic, assignment_word))
            redirection_operand = False
            word.clear()
            dynamic = False
            started = False
            plain_prefix = True
            assignment_word = False

    def finish_command():
        nonlocal words, assigned
        finish_word()
        if redirection_operand:
            raise _UnknownShell()
        has_command = bool(words)
        while words and words[0][2]:
            assigned = True
            words = words[1:]
        if words:
            name, expanded, _ = words[0]
            name = os.path.basename(name)
            if expanded or not _NAME.fullmatch(name) or name in _CONTROL:
                raise _UnknownShell()
            families.append(name)
        words = []
        return has_command

    while index < len(text):
        char = text[index]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                word.append(char)
        elif char == "\\":
            plain_prefix = False
            index += 1
            if index >= len(text):
                raise _UnknownShell()
            if text[index] != "\n":
                word.append(text[index])
        # Unsupported expansions can contain operators that are not command
        # separators. Do not let their argument values become command names.
        elif (char == "`" or text.startswith(("${", "$((", "$["), index)
              or (quote is None and text.startswith(("$'", '$"'), index))):
            raise _UnknownShell()
        elif text.startswith("$(", index):
            end = _substitution_end(text, index + 2, depth + 1)
            description = describe_shell(text[index + 2:end], _depth=depth + 1)
            if description["classification"] == "unknown":
                raise _UnknownShell()
            nested.extend(description["families"])
            word.append("__substitution__")
            dynamic = True
            plain_prefix = False
            index = end
        elif char == "$":
            variable = _VARIABLE.match(text, index)
            word.append("__variable__")
            dynamic = True
            plain_prefix = False
            if variable:
                index = variable.end() - 1
        elif quote == '"':
            if char == '"':
                quote = None
            else:
                word.append(char)
        elif char in "'\"":
            started = True
            plain_prefix = False
            quote = char
        elif char == "#" and not word and not started:
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline - 1
        elif char in "(){}":
            raise _UnknownShell()
        elif char in ";&|\n":
            if char == "\n":
                if finish_command():
                    required_command = False
            else:
                end = index + 1
                while end < len(text) and text[end] in ";&|":
                    end += 1
                separator = text[index:end]
                if separator not in {";", "&", "&&", "|", "||"} or not finish_command():
                    raise _UnknownShell()
                required_command = separator in {"&&", "|", "||"}
                index = end - 1
        elif char in "<>":
            if redirection_operand or text.startswith("<<", index) or text.startswith(">>", index):
                raise _UnknownShell()
            if word and _DESCRIPTOR.fullmatch("".join(word)) and plain_prefix and not dynamic:
                descriptor = "".join(word)
                # Out-of-range numbers can be command words, not descriptors.
                # Do not discard them and promote an argument to a command.
                if len(descriptor) > 10 or int(descriptor) > 2**31 - 1:
                    raise _UnknownShell()
                word.clear()
                started = False
            finish_word()
            redirection_operand = True
        elif char in " \t":
            finish_word()
        else:
            # Only a literal, unquoted NAME= prefix is an assignment word.
            # Quotes and expansions in its value do not change that prefix.
            if char == "=" and plain_prefix and _ASSIGNMENT.fullmatch("".join(word) + "="):
                assignment_word = True
            word.append(char)
        index += 1
    if quote is not None or redirection_operand:
        raise _UnknownShell()
    if not finish_command() and required_command:
        raise _UnknownShell()
    if not families and not nested:
        if not assigned:
            raise _UnknownShell()
        families.append("assignment")
    return families, nested
