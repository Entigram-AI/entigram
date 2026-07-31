import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_homebrew_formula.py"
SPEC = importlib.util.spec_from_file_location("update_homebrew_formula", SCRIPT_PATH)
update_homebrew_formula = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_homebrew_formula)


class TestHomebrewFormulaUpdate(unittest.TestCase):
    def test_updates_only_top_level_source_checksum(self):
        old_sha = "0" * 64
        resource_sha = old_sha
        new_sha = "1" * 64
        new_url = "https://files.pythonhosted.org/packages/aa/bb/example/entigram_ai-1.3.2.tar.gz"
        formula = f'''class Etg < Formula
  include Language::Python::Virtualenv

  desc "Schema-first semantic governance layer for enterprise agents"
  homepage "https://api.entigram.ai"
  url "https://files.pythonhosted.org/packages/old/entigram_ai-1.3.1.tar.gz"
  sha256 "{old_sha}"
  license "Apache-2.0"

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/f3/certifi-2026.5.20.tar.gz"
    sha256 "{resource_sha}"
  end
end
'''

        with tempfile.TemporaryDirectory() as tmpdir:
            formula_path = Path(tmpdir) / "etg.rb"
            formula_path.write_text(formula)

            update_homebrew_formula.update_formula_source(formula_path, new_url, new_sha)

            updated = formula_path.read_text()

        self.assertIn(f'  url "{new_url}"', updated)
        self.assertIn(f'  sha256 "{new_sha}"', updated)
        self.assertIn(f'    sha256 "{resource_sha}"', updated)
        self.assertEqual(updated.count(new_sha), 1)

    def test_selects_source_distribution_from_pypi_metadata(self):
        release = {
            "urls": [
                {
                    "packagetype": "bdist_wheel",
                    "filename": "entigram_ai-1.3.2-py3-none-any.whl",
                    "digests": {"sha256": "wheel"},
                    "url": "https://example.test/wheel",
                },
                {
                    "packagetype": "sdist",
                    "filename": "entigram_ai-1.3.2.tar.gz",
                    "digests": {"sha256": "sdist"},
                    "url": "https://example.test/sdist",
                },
            ]
        }

        self.assertEqual(
            update_homebrew_formula.select_sdist(release),
            ("https://example.test/sdist", "sdist"),
        )

    def test_updates_homebrew_python_runtime(self):
        formula = '''class Etg < Formula
  include Language::Python::Virtualenv

  depends_on "python@3.12"

  def install
    venv = virtualenv_create(libexec, "python3.12")
  end
end
'''

        with tempfile.TemporaryDirectory() as tmpdir:
            formula_path = Path(tmpdir) / "etg.rb"
            formula_path.write_text(formula)

            update_homebrew_formula.update_formula_python_runtime(formula_path)

            updated = formula_path.read_text()

        self.assertIn('depends_on "python@3.14"', updated)
        self.assertIn('virtualenv_create(libexec, "python3.14")', updated)
        self.assertNotIn("python@3.12", updated)
        self.assertNotIn("python3.12", updated)

    def test_select_sdist_reports_files_seen_when_missing(self):
        release = {
            "urls": [
                {
                    "packagetype": "bdist_wheel",
                    "filename": "entigram_ai-1.7.5-py3-none-any.whl",
                    "digests": {"sha256": "wheel"},
                    "url": "https://example.test/wheel",
                }
            ]
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "entigram_ai-1.7.5-py3-none-any.whl",
        ):
            update_homebrew_formula.select_sdist(release)

    def test_load_pypi_sdist_retries_until_source_metadata_is_visible(self):
        wheel_only = {
            "urls": [
                {
                    "packagetype": "bdist_wheel",
                    "filename": "entigram_ai-1.7.5-py3-none-any.whl",
                    "digests": {"sha256": "wheel"},
                    "url": "https://example.test/wheel",
                }
            ]
        }
        with_sdist = {
            "urls": [
                {
                    "packagetype": "sdist",
                    "filename": "entigram_ai-1.7.5.tar.gz",
                    "digests": {"sha256": "sdist"},
                    "url": "https://example.test/sdist",
                }
            ]
        }
        releases = [wheel_only, with_sdist]
        sleeps = []
        original_loader = update_homebrew_formula.load_pypi_release
        original_sleep = update_homebrew_formula.time.sleep

        def fake_loader(package_name, version, *, attempts=12, sleep_seconds=10):
            return releases.pop(0)

        try:
            update_homebrew_formula.load_pypi_release = fake_loader
            update_homebrew_formula.time.sleep = sleeps.append

            result = update_homebrew_formula.load_pypi_sdist(
                "entigram-ai",
                "1.7.5",
                attempts=2,
                sleep_seconds=0,
            )
        finally:
            update_homebrew_formula.load_pypi_release = original_loader
            update_homebrew_formula.time.sleep = original_sleep

        self.assertEqual(result, ("https://example.test/sdist", "sdist"))
        self.assertEqual(sleeps, [0])

    def test_package_resource_names_include_dash_and_underscore_forms(self):
        self.assertEqual(
            update_homebrew_formula.package_resource_names("entigram-ai"),
            {"entigram-ai", "entigram_ai"},
        )

    def test_filters_rust_backed_resources_to_native_dependencies(self):
        resources = '''  resource "pydantic" do
    url "https://files.pythonhosted.org/packages/pydantic.tar.gz"
    sha256 "pydantic"
  end

  resource "pydantic_core" do
    url "https://files.pythonhosted.org/packages/pydantic_core.tar.gz"
    sha256 "core"
  end

  resource "rpds-py" do
    url "https://files.pythonhosted.org/packages/rpds_py.tar.gz"
    sha256 "rpds"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/httpx.tar.gz"
    sha256 "httpx"
  end
'''

        filtered, deps = update_homebrew_formula.filter_native_resources(resources)

        self.assertEqual(deps, ["pydantic", "rpds-py"])
        self.assertNotIn('resource "pydantic"', filtered)
        self.assertNotIn('resource "pydantic_core"', filtered)
        self.assertNotIn('resource "rpds-py"', filtered)
        self.assertIn('resource "httpx"', filtered)
        self.assertTrue(filtered.startswith('resource "httpx" do'))
        self.assertNotIn("\n\n\n", filtered)

    def test_filters_current_package_resource_without_native_dependency(self):
        resources = '''resource "entigram-ai" do
  url "https://files.pythonhosted.org/packages/entigram_ai-1.7.5.tar.gz"
  sha256 "self"
end

resource "httpx" do
  url "https://files.pythonhosted.org/packages/httpx.tar.gz"
  sha256 "httpx"
end
'''

        filtered, deps = update_homebrew_formula.filter_native_resources(
            resources,
            excluded_resource_names=update_homebrew_formula.package_resource_names("entigram-ai"),
        )

        self.assertEqual(deps, [])
        self.assertNotIn('resource "entigram-ai"', filtered)
        self.assertIn('resource "httpx"', filtered)

    def test_render_dependency_block_does_not_inject_unneeded_cryptography(self):
        block = update_homebrew_formula.render_dependency_block(
            ["pydantic", "rpds-py"],
            'resource "httpx" do\n  url "https://example.test/httpx.tar.gz"\nend',
        )

        self.assertIn('  depends_on "pydantic"', block)
        self.assertIn('  depends_on "rpds-py"', block)
        self.assertNotIn('depends_on "cryptography"', block)
        self.assertIn('  resource "setuptools" do', block)
        self.assertIn('  resource "httpx" do', block)
        self.assertNotIn('    resource "httpx" do', block)

    def test_replace_formula_resources_produces_brew_style_layout(self):
        formula = '''class Etg < Formula
  include Language::Python::Virtualenv

  desc "Schema-first semantic governance layer for enterprise agents"
  homepage "https://entigram.ai"
  url "https://files.pythonhosted.org/packages/old/entigram_ai-1.7.4.tar.gz"
  sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  license "Apache-2.0"

  depends_on "libyaml"
  depends_on "python@3.14"

  def install
  end
end
'''
        poet_output = '''  resource "entigram-ai" do
    url "https://files.pythonhosted.org/packages/entigram_ai-1.7.3.tar.gz"
    sha256 "self"
  end

  resource "cryptography" do
    url "https://files.pythonhosted.org/packages/cryptography.tar.gz"
    sha256 "cryptography"
  end

  resource "cffi" do
    url "https://files.pythonhosted.org/packages/cffi.tar.gz"
    sha256 "cffi"
  end

  resource "pydantic_core" do
    url "https://files.pythonhosted.org/packages/pydantic_core.tar.gz"
    sha256 "core"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/httpx.tar.gz"
    sha256 "httpx"
  end
'''

        updated = update_homebrew_formula.replace_formula_resources(
            formula,
            "entigram-ai",
            poet_output,
        )

        self.assertIn('depends_on "pydantic"', updated)
        self.assertIn('resource "setuptools"', updated)
        self.assertNotIn('resource "entigram-ai"', updated)
        self.assertNotIn('resource "pydantic_core"', updated)
        self.assertIn('resource "httpx"', updated)
        self.assertNotIn('    resource "httpx" do', updated)
        self.assertNotIn("\n\n\n", updated)
        self.assertEqual(
            [
                line.strip()
                for line in updated.splitlines()
                if line.startswith("  depends_on ")
            ],
            [
                'depends_on "cffi"',
                'depends_on "cryptography"',
                'depends_on "libyaml"',
                'depends_on "pydantic"',
                'depends_on "python@3.14"',
            ],
        )
        self.assertEqual(
            update_homebrew_formula.replace_formula_resources(
                updated,
                "entigram-ai",
                poet_output,
            ),
            updated,
        )


if __name__ == "__main__":
    unittest.main()
