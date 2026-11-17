import hashlib
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
UPDATER = runpy.run_path(str(ROOT / "scripts/update_formula.py"))
update_formula = UPDATER["update_formula"]
verified_download = UPDATER["verified_download"]


def test_updates_both_downloads_and_resets_revision():
    original = (ROOT / "Formula/ozm.rb").read_text(encoding="utf-8")
    # Model an existing packaging-only revision even after CI updates the real formula.
    original = original.replace('  depends_on "python@3.12"',
                                '  revision 99\n  depends_on "python@3.12"', 1)
    sdist = ("https://example.com/ozm-2099.1.1.1.tar.gz", "a" * 64)
    wheel = ("https://example.com/ozm-2099.1.1.1-py3-none-macosx_14_0_universal2.whl", "b" * 64)
    updated = update_formula(original, sdist, wheel)
    assert f'  url "{sdist[0]}"\n  sha256 "{sdist[1]}"' in updated
    assert f'      url "{wheel[0]}", using: :nounzip\n      sha256 "{wheel[1]}"' in updated
    assert "  revision " not in updated
    assert updated.split('  resource "click"', 1)[1] == original.split('  resource "click"', 1)[1]
    assert update_formula(updated, sdist, wheel) == updated


def test_keeps_the_revision_when_the_release_has_not_changed():
    original = (ROOT / "Formula/ozm.rb").read_text(encoding="utf-8")
    original = original.replace('  depends_on "python@3.12"',
                                '  revision 99\n  depends_on "python@3.12"', 1)
    lines = original.splitlines()

    def download(indent):
        return tuple(next(line.split('"')[1] for line in lines if line.startswith(f'{indent}{key} "'))
                     for key in ("url", "sha256"))

    assert update_formula(original, download("  "), download("      ")) == original
    with pytest.raises(ValueError, match="one ozm-app resource"):
        update_formula(original.replace('resource "ozm-app"', 'resource "unexpected"'),
                       download("  "), download("      "))


def test_accepts_only_the_matching_non_yanked_pypi_artifact(tmp_path):
    artifact = tmp_path / "ozm-2099.1.1.1-py3-none-macosx_14_0_universal2.whl"
    artifact.write_bytes(b"release wheel")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    url = f"https://files.pythonhosted.org/packages/ab/cd/{artifact.name}"
    meta = {"filename": artifact.name, "yanked": False, "digests": {"sha256": digest}, "url": url}
    assert verified_download(artifact, [meta]) == (url, digest)
    with pytest.raises(ValueError, match="one published file"):
        verified_download(artifact, [meta | {"yanked": True}])
    with pytest.raises(ValueError, match="Unexpected PyPI URL"):
        verified_download(artifact, [meta | {"url": url.replace(".org", ".example")}])
    artifact.write_bytes(b"different wheel")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verified_download(artifact, [meta])
