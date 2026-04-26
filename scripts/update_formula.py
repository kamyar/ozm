#!/usr/bin/env python3
"""Update Formula/ozm.rb with a new version, URL, and SHA256."""

import re
import sys


def update_formula(formula_path: str, url: str, sha256: str) -> None:
    with open(formula_path) as f:
        content = f.read()

    original = content

    url_pattern = re.compile(r'^(\s*url\s+)"[^"]+"', re.MULTILINE)
    sha_pattern = re.compile(r'^(\s*sha256\s+)"[0-9a-f]{64}"', re.MULTILINE)

    if not url_pattern.search(content):
        print("ERROR: could not find url field in formula", file=sys.stderr)
        sys.exit(1)
    if not sha_pattern.search(content):
        print("ERROR: could not find sha256 field in formula", file=sys.stderr)
        sys.exit(1)

    content = url_pattern.sub(rf'\1"{url}"', content, count=1)
    content = sha_pattern.sub(rf'\1"{sha256}"', content, count=1)

    if content == original:
        print("Formula already up to date")
        sys.exit(0)

    with open(formula_path, "w") as f:
        f.write(content)

    print(f"Updated {formula_path}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <formula> <url> <sha256>", file=sys.stderr)
        sys.exit(1)
    update_formula(sys.argv[1], sys.argv[2], sys.argv[3])
