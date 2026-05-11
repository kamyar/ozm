class Ozm < Formula
  include Language::Python::Virtualenv

  desc "Content-aware script execution gate and git rule enforcer for AI agents"
  homepage "https://github.com/kamyar/ozm"
  url "https://files.pythonhosted.org/packages/4c/78/12c32c89fcfb279109d66317a89973fdddca4b93150728967d20d987e610/ozm-2026.5.11.4.tar.gz"
  sha256 "51a371c418e59d7947fd725b9f61a7a4603554410c8f4b34a3315a4a68e9033e"
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
    assert_match version.to_s, shell_output("#{bin}/ozm version")
  end
end
