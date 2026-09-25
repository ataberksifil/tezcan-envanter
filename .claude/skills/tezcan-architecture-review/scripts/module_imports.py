"""Read-only import map between Tezcan Envanter's Django apps.

Parses Python files with ``ast`` (nothing is imported or executed) and prints
which app imports which, then checks the dependency rules of AGENTS.md §6–7.
Tests and migrations are listed separately because the rules target runtime code.

Usage (from the project root):
    python .claude/skills/tezcan-architecture-review/scripts/module_imports.py [--json]
"""

from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

APPS = (
    "accounts", "audit", "catalog", "core", "corrections", "counting",
    "identification", "imports", "inventory", "locations", "procurement", "reports",
)

# (importer, imported) pairs AGENTS.md §6–7 forbids in runtime code.
FORBIDDEN = {
    ("core", app) for app in APPS if app != "core"
} | {
    ("catalog", "identification"), ("inventory", "identification"), ("locations", "identification"),
    ("audit", "accounts"), ("audit", "catalog"), ("audit", "inventory"), ("audit", "corrections"),
    ("audit", "counting"), ("audit", "imports"), ("audit", "procurement"), ("audit", "locations"),
}
# Allowed only for reading; flagged for manual review (mutation must go through inventory services).
REVIEW = {("catalog", "inventory"), ("locations", "inventory"), ("reports", "inventory")}


def app_of(path: Path, root: Path) -> str | None:
    first = path.relative_to(root).parts[0]
    return first if first in APPS else None


def imported_apps(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top in APPS:
                found.add(top)
    return found


def main() -> int:
    root = Path.cwd()
    edges: dict[str, dict[tuple[str, str], list[str]]] = {
        "runtime": defaultdict(list), "tests": defaultdict(list), "migrations": defaultdict(list),
    }
    for path in root.rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts or ".claude" in path.parts:
            continue
        src_app = app_of(path, root)
        if src_app is None:
            continue
        kind = "migrations" if "migrations" in path.parts else "tests" if "tests" in path.parts else "runtime"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for dst in imported_apps(tree):
            if dst != src_app:
                edges[kind][(src_app, dst)].append(str(path.relative_to(root)).replace("\\", "/"))

    runtime = edges["runtime"]
    violations = {k: v for k, v in runtime.items() if k in FORBIDDEN}
    review = {k: v for k, v in runtime.items() if k in REVIEW}
    cycles = sorted({tuple(sorted(k)) for k in runtime if (k[1], k[0]) in runtime})

    if "--json" in sys.argv:
        print(json.dumps({
            "runtime_edges": {f"{a}->{b}": len(v) for (a, b), v in sorted(runtime.items())},
            "violations": {f"{a}->{b}": v for (a, b), v in violations.items()},
            "review": {f"{a}->{b}": v for (a, b), v in review.items()},
            "two_way_pairs": [list(c) for c in cycles],
        }, indent=1, ensure_ascii=False))
    else:
        print("Runtime app imports (importer -> imported : files)")
        for (a, b), files in sorted(runtime.items()):
            print(f"  {a} -> {b} : {len(files)}")
        print(f"\nForbidden by AGENTS.md sections 6-7: {len(violations)}")
        for (a, b), files in sorted(violations.items()):
            print(f"  {a} -> {b}: {', '.join(sorted(files))}")
        print(f"\nRead-only allowed, review for mutation: {len(review)}")
        for (a, b), files in sorted(review.items()):
            print(f"  {a} -> {b}: {', '.join(sorted(files))}")
        print(f"\nTwo-way app pairs (possible cycle): {len(cycles)}")
        for pair in cycles:
            print(f"  {pair[0]} <-> {pair[1]}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
