"""Label isolation: detectors and the ML layer must never see ground-truth labels.

Static check: no module under `tripwire/detectors` or `tripwire/ml` imports
`tripwire.eval` or `tripwire.sim.labels`, mentions the labels directory, or reads the
`episodes` a simulated day returns.
Runtime check: importing every detector/ML module in a fresh interpreter does not load
either of those modules.
"""

import ast
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "tripwire"
GUARDED = ("detectors", "ml")
FORBIDDEN_MODULES = ("tripwire.eval", "tripwire.sim.labels")
FORBIDDEN_STRINGS = (
    "eval/labels",
    "eval\\labels",
    "labels_path",
    "read_labels",
    "LABELS_DIR",
    ".episodes",  # ground truth carried on DayResult; ML trains on clean days only
)


def guarded_files() -> list[Path]:
    files = [p for pkg in GUARDED for p in (PACKAGE_ROOT / pkg).rglob("*.py")]
    assert files, "guarded packages not found"
    return files


def imported_modules(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_guarded_packages_do_not_import_labels() -> None:
    offenders = []
    for path in guarded_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in imported_modules(tree):
            if name.startswith(FORBIDDEN_MODULES):
                offenders.append(f"{path.relative_to(PACKAGE_ROOT)}: imports {name}")
    assert not offenders, offenders


def test_guarded_packages_do_not_reference_label_files() -> None:
    offenders = []
    for path in guarded_files():
        source = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(PACKAGE_ROOT)}: mentions {s!r}"
            for s in FORBIDDEN_STRINGS
            if s in source
        )
    assert not offenders, offenders


def test_importing_guarded_packages_does_not_load_labels() -> None:
    modules = [
        ".".join(("tripwire", *p.relative_to(PACKAGE_ROOT).with_suffix("").parts)).removesuffix(
            ".__init__"
        )
        for p in guarded_files()
    ]
    script = (
        "import importlib, sys\n"
        f"for m in {modules!r}: importlib.import_module(m)\n"
        f"bad = [m for m in sys.modules if m.startswith({FORBIDDEN_MODULES!r})]\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
