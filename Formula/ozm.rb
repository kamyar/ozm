class Ozm < Formula
  include Language::Python::Virtualenv

  desc "Content-aware script execution gate and git rule enforcer for AI agents"
  homepage "https://github.com/kamyar/ozm"
  url "https://files.pythonhosted.org/packages/a6/30/a08da11a9ba39237ef28dd7eb150979d738e3c9be31a226db85570d09b58/ozm-2026.4.26.2.tar.gz"
  sha256 "1ef64b82ebbce2078af8eec4042efdce0f61d7a86bd7671651c1347057a78bbd"
  depends_on "python@3.12"

  resource "click" do
    url "https://files.pythonhosted.org/packages/bb/63/f9e1ea081ce35720d8b92acde70daaedace594dc93b693c869e0d5910718/click-8.3.3.tar.gz"
    sha256 "398329ad4837b2ff7cbe1dd166a4c0f8900c3ca3a218de04466f38f6497f18a2"
  end

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/c3/b2/bc9c9196916376152d655522fdcebac55e66de6603a76a02bca1b6414f6c/pygments-2.20.0.tar.gz"
    sha256 "6757cd03768053ff99f3039c1a36d6c0aa0b263438fcab17520b30a303a82b5f"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "Content-aware script execution gate", shell_output("#{bin}/ozm --help")
  end
end
