"""Phase 2 checks: the project skeleton is complete and follows its rules.

Run from the project folder with:
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

Uses only Python's built-in ``unittest`` and ``tomllib`` (no add-ons).
"""

import importlib
import sys
import tomllib
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"

# The package is not installed yet, so tell Python where to find it.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PACKAGES = [
    "quantlab",
    "quantlab.core",
    "quantlab.markets",
    "quantlab.markets.crypto",
    "quantlab.data",
    "quantlab.stats",
    "quantlab.experiments",
    "quantlab.agents",
]

# Folders that must stay market-neutral so futures can be added later.
MARKET_NEUTRAL = ["core", "stats", "experiments", "agents"]


class TestProjectStructure(unittest.TestCase):

    def test_required_files_exist(self):
        for name in ["README.md", ".gitignore", "pyproject.toml",
                     "config/settings.toml", "data/README.md"]:
            with self.subTest(file=name):
                self.assertTrue((PROJECT_ROOT / name).is_file(), f"missing {name}")

    def test_all_packages_import(self):
        for name in PACKAGES:
            with self.subTest(package=name):
                importlib.import_module(name)

    def test_settings_file_is_valid(self):
        with open(PROJECT_ROOT / "config" / "settings.toml", "rb") as f:
            settings = tomllib.load(f)
        self.assertEqual(settings["project"]["mode"], "research")
        self.assertEqual(settings["time"]["timezone"], "UTC")
        self.assertEqual(settings["markets"]["crypto"]["symbols"],
                         ["BTC", "ETH", "SOL", "XRP"])

    def test_market_neutral_code_does_not_import_markets(self):
        """core/stats/experiments/agents must never depend on a specific market."""
        for folder in MARKET_NEUTRAL:
            for py_file in (SRC / "quantlab" / folder).rglob("*.py"):
                text = py_file.read_text(encoding="utf-8")
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(("import ", "from ")):
                        with self.subTest(file=str(py_file.relative_to(SRC))):
                            self.assertNotIn("quantlab.markets", stripped)

    def test_downloaded_data_is_not_uploaded(self):
        ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".venv/", ignore)
        self.assertIn("data/*", ignore)


if __name__ == "__main__":
    unittest.main()
