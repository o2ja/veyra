"""import-linter contracts hold, and a deliberate violation is caught (S0 acceptance)."""

import configparser
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _lint_imports(root: Path) -> subprocess.CompletedProcess[str]:
    lint = Path(sys.executable).with_name("lint-imports")
    return subprocess.run(
        [str(lint), "--config", "importlinter.ini", "--no-cache"],
        cwd=root,
        env={"PYTHONPATH": str(root / "src"), "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )


def test_contracts_are_kept() -> None:
    result = _lint_imports(BACKEND)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


def test_deliberate_violation_is_caught(tmp_path: Path) -> None:
    shutil.copytree(BACKEND / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(BACKEND / "importlinter.ini", tmp_path)
    (tmp_path / "src" / "rebate_engine" / "leak.py").write_text("from rebate.core import clock\n")

    result = _lint_imports(tmp_path)
    assert result.returncode != 0
    assert "rebate_engine is pure" in result.stdout and "BROKEN" in result.stdout
    assert "rebate_engine.leak -> rebate.core" in result.stdout


def test_every_business_module_has_a_protected_contract() -> None:
    modules = {
        p.name for p in (BACKEND / "src" / "rebate" / "modules").iterdir() if (p / "__init__.py").exists()
    }
    parser = configparser.ConfigParser()
    parser.read(BACKEND / "importlinter.ini")
    protected = {
        line.strip()
        for section in parser.sections()
        if parser[section].get("type") == "protected"
        for line in parser[section]["protected_modules"].split()
    }
    missing = {m for m in modules if f"rebate.modules.{m}.*" not in protected}
    assert not missing, f"add a protected contract to importlinter.ini for: {sorted(missing)}"
