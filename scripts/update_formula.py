#!/usr/bin/env python3
"""Update the Homebrew formula from the exact distributions published to PyPI."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen


APP_RESOURCE = re.compile(r'(?ms)(^    resource "ozm-app" do\n)(.*?)(^    end$)')


def update_download(content: str, indent: str, url: str, digest: str) -> str:
    for name, value in (("url", url), ("sha256", digest)):
        field = re.compile(rf'^({indent}{name} )"[^"\n]+"(, using: :nounzip)?$', re.MULTILINE)
        content, count = field.subn(lambda match: f'{match[1]}"{value}"{match[2] or ""}', content)
        if count != 1:
            raise ValueError(f"Expected one {name} field in the formula section")
    return content


def update_formula(formula: str, sdist: tuple[str, str], wheel: tuple[str, str]) -> str:
    if not re.search(rf'^  url "{re.escape(sdist[0])}"$', formula, re.MULTILINE):
        # A Homebrew revision applies only to the previous upstream version.
        formula = re.sub(r"^  revision \d+\n", "", formula, flags=re.MULTILINE)
    formula = update_download(formula, "  ", *sdist)
    formula, count = APP_RESOURCE.subn(
        lambda match: match[1] + update_download(match[2], "      ", *wheel) + match[3], formula
    )
    if count != 1:
        raise ValueError("Expected one ozm-app resource in the formula")
    return formula


def published_files(version: str) -> list[dict]:
    for attempt in range(5):
        try:
            with urlopen(f"https://pypi.org/pypi/ozm/{version}/json", timeout=15) as response:
                return json.load(response)["urls"]
        except URLError:
            if attempt == 4:
                raise
            time.sleep(15)
    raise AssertionError("Unreachable")


def verified_download(artifact: Path, files: list[dict]) -> tuple[str, str]:
    matches = [f for f in files if f["filename"] == artifact.name and not f["yanked"]]
    if len(matches) != 1:
        raise ValueError(f"Expected one published file named {artifact.name}")
    published = matches[0]
    with artifact.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    if digest != published["digests"]["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {artifact.name}")
    url = published["url"]
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "files.pythonhosted.org"
            or not re.fullmatch(r"/packages/[a-f0-9/]+/" + re.escape(artifact.name), parsed.path)
            or parsed.params or parsed.query or parsed.fragment):
        raise ValueError(f"Unexpected PyPI URL for {artifact.name}")
    return url, digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("formula", type=Path)
    parser.add_argument("dist", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}\.\d+\.\d+\.\d+", args.version):
        parser.error("Invalid OZM release version")
    try:
        files = published_files(args.version)
        base = f"ozm-{args.version}"
        sdist = verified_download(args.dist / f"{base}.tar.gz", files)
        wheel = verified_download(args.dist / f"{base}-py3-none-macosx_14_0_universal2.whl", files)
        original = args.formula.read_text(encoding="utf-8")
        updated = update_formula(original, sdist, wheel)
        if updated != original:
            args.formula.write_text(updated, encoding="utf-8")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Formula update failed: {error}\n")


if __name__ == "__main__":
    main()
