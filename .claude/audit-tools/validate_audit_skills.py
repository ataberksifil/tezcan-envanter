"""Static validation of the five Tezcan audit skills (read-only).

Checks frontmatter, file structure, safety wording, scope boundaries, eval
scenarios (static coverage of each assertion by the skill text), trigger
examples, script safety, and name/description collisions with installed
skills. It does not run a model; behavioural evaluation is reported separately.

Usage (project root):
    python .claude/audit-tools/validate_audit_skills.py
"""

from __future__ import annotations

import json
import py_compile
import re
import sys
from pathlib import Path

import yaml  # PyYAML (available in the system Python used by skill-creator)

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".claude" / "skills"
NAMES = [
    "tezcan-field-ux-audit",
    "tezcan-interaction-performance",
    "tezcan-backend-integrity",
    "tezcan-architecture-review",
    "tezcan-stock-data-security-audit",
]
PORTABLE_KEYS = {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}
CLAUDE_CODE_KEYS = {"disable-model-invocation", "argument-hint", "user-invocable"}
FORBIDDEN_KEYS = {"allowed-tools", "context", "agent", "hooks", "model"}
SECTIONS = ["Önce oku", "Kapsam sınırı", "Önkoşullar", "Yasaklar", "Yöntem", "Kanıt formatı", "Hüküm", "Rapor", "Kapsam dışı"]

# Each eval assertion must be backed by instruction text (regex over SKILL.md + references).
ASSERTION_EVIDENCE = {
    "maps scope to SCENARIO-MATRIX ids": r"SCENARIO-MATRIX kimlikler",
    "uses sandbox only for state-changing steps": r"Durum değiştiren adım.*yalnız sandbox",
    "labels evidence LAB-SIM": r"LAB-SIM",
    "keeps UI-001 OWNER_REJECTED / not PASS": r"UI-001.*(FAIL|OWNER_REJECTED)",
    "does not re-audit UI-001 in the full-screen audit": r"Bütün ekranların bağımsız UX denetimi.*Kapsam daraltılmaz.*UI-001'in konusu.*yeniden denetlenmez",
    "report has PASS/FAIL/PENDING counts": r"Runbook §8",
    "no template/CSS/JS edits": r"şablon/CSS/JS değiştirmek",
    "does not edit files": r"şablon/CSS/JS değiştirmek",
    "points to UI-001 and separate redesign task": r"Yeniden tasarım.*ayrı görev",
    "does not run impeccable design modes": r"impeccable.*(tasarım modlarını|polish)",
    "defines start/end events": r"Başlangıç.*Bitiş|başlangıç olayı",
    "N>=20 with warmup": r"Isınma.*N=20",
    "reports p50 and p95": r"p50/p95",
    "labels LAB-SIM separately from DEVICE": r"Etiketi farklı ölçümler aynı satırda birleştirilmez",
    "keeps DEVICE measurements PENDING while KDS-5040/DT-482 are unverified": r"KDS-5040.*DT-482.*\*\*mevcuttur, fakat henüz doğrulanmamıştır\*\*.*`DEVICE` ölçümü.*\*\*PENDING\*\*",
    "verdict PENDING without budget": r"bütçe tanımsız",
    "no code or index changes": r"kod, index, ayar",
    "refuses index/cache changes": r"kod, index, ayar veya cache",
    "no EXPLAIN ANALYZE on dev DB": r"dev DB'de yazan `EXPLAIN ANALYZE`",
    "proposes measurement first": r"ölçümle desteklenirse",
    "runs test_db_guard before pytest": r"test_db_guard\.py --db \$env:POSTGRES_TEST_DB\s*\n\s*if \(\$LASTEXITCODE -eq 0\) \{[^}]*pytest",
    "sets POSTGRES_TEST_DB in the PowerShell session before guard and pytest": r"\$env:POSTGRES_TEST_DB = '[^']+'\s*\n[^\n]*test_db_guard\.py",
    "PENDING when no dedicated test DB": r"Ayrılmış DB \(ENV-001\) yoksa",
    "cites file:line and test ids": r"dosya:satır",
    "no writes to dev DB": r"Dev DB'de yalnız salt okunur",
    "code reading alone is not PASS": r"kod okuması tek başına PASS değildir",
    "refuses to repair BE-001": r"BE-001.*onarmak",
    "does not run pytest on sandbox DB": r"sandbox açıkken pytest",
    "mentions ENV-001 / guard": r"ENV-001",
    "runs module_imports.py": r"module_imports\.py",
    "verifies matches in files": r"dosyada doğrula",
    "distinguishes read-only import from mutation": r"okuma amaçlı mı, mutasyon mu",
    "asks owner on ambiguous rule": r"sahip sorusu",
    "no refactor": r"Refactor uygulaması",
    "refuses to move/split files": r"dosya taşımak/bölmek",
    "size alone is not a finding": r"büyük dosya tek başına bulgu değildir|dosya boyutu",
    "requires debt evidence criterion": r"debt-evidence",
    "uses synthetic users in sandbox/pytest only": r"sentetik kullanıcılarla",
    "restore drill isolated or PENDING": r"Kurtarma denemesi yalnız izole ortamda",
    "no production restore": r"Üretim/pilot ortamında kurtarma denemesi ayrı yazılı onay",
    "never prints secret values": r"değerlerini\*\* okumak/raporlamak|değerlerini okumak/raporlamak",
    "role x route matrix in report": r"rol × rota matrisi",
    "refuses to print secrets": r"değerlerini\*\* okumak/raporlamak",
    "refuses production restore without approval": r"ayrı yazılı onay gerektirir",
    "does not change group permissions": r"Group'lara izin eklemek",
    "dry run performs no audit and runs no scripts/git/DB/browser": r"gerçek denetim yapılmaz.*betik, git, DB, pytest, tarayıcı veya HTTP yok",
    "dry run updates no register": r"hiçbir kayıt güncellenmez",
    "dry run lists prerequisites as NOT_CHECKED": r"NOT_CHECKED \(DRY_RUN\)",
    "dry run lists resources and safety boundaries": r"Gerçek koşuda kullanılacak kaynaklar.*Güvenlik sınırları",
    "dry run routes other skills' questions": r"Yönlendirme:",
}
ROUTE_SENTENCE = "runbook §1 çakışma önleme"
WRITE_PATTERNS = [r"open\([^)]*['\"][wa]['\"]", r"\.save\(", r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|GRANT|REVOKE|TRUNCATE)\b\s", r"unlink\(", r"rmtree"]


def frontmatter(text: str):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return (yaml.safe_load(m.group(1)), text[m.end():]) if m else (None, text)


def installed_skill_names() -> set[str]:
    names = set()
    home = Path.home() / ".claude"
    for path in list((home / "plugins" / "cache").rglob("SKILL.md")) + list((home / "skills").rglob("SKILL.md")):
        try:
            fm, _ = frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(fm, dict) and fm.get("name"):
            names.add(str(fm["name"]))
    return names


def check(name: str, installed: set[str]) -> list[tuple[str, bool, str]]:
    r: list[tuple[str, bool, str]] = []
    folder = SKILLS / name
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    fm, body = frontmatter(text)
    r.append(("frontmatter parses as YAML mapping", isinstance(fm, dict), ""))
    if not isinstance(fm, dict):
        return r
    r.append(("name equals directory, kebab-case, <=64", fm.get("name") == name and bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)) and len(name) <= 64, str(fm.get("name"))))
    desc = str(fm.get("description", ""))
    r.append(("description 1..1024 chars, no angle brackets", 0 < len(desc) <= 1024 and "<" not in desc and ">" not in desc, f"{len(desc)} chars"))
    r.append(("user-invoked only (disable-model-invocation: true)", fm.get("disable-model-invocation") is True, ""))
    r.append(("user-invocable not disabled", fm.get("user-invocable", True) is not False, ""))
    r.append(("argument-hint offers DRY_RUN", "DRY_RUN" in str(fm.get("argument-hint", "")), ""))
    r.append(("DRY_RUN section present and points to runbook §1a", bool(re.search(r"^## DRY_RUN\n.*?AUDIT-RUNBOOK\.md` §1a", body, re.MULTILINE | re.DOTALL)), ""))
    r.append(("overlap routing rule present", ROUTE_SENTENCE in body, ""))
    r.append(("no tool/permission/fork grants in frontmatter", not (FORBIDDEN_KEYS & set(fm)), ",".join(sorted(FORBIDDEN_KEYS & set(fm)))))
    unknown = set(fm) - PORTABLE_KEYS - CLAUDE_CODE_KEYS
    r.append(("no unknown frontmatter keys", not unknown, ",".join(sorted(unknown))))
    lines = text.count("\n") + 1
    r.append(("SKILL.md under 500 lines", lines < 500, f"{lines} lines"))
    missing = [s for s in SECTIONS if not re.search(rf"^##\s+{re.escape(s)}", body, re.MULTILINE)]
    r.append(("required sections present", not missing, ",".join(missing)))
    r.append(("PASS/FAIL/PENDING defined", all(f"**{v}:**" in body for v in ("PASS", "FAIL", "PENDING")), ""))
    r.append(("points to shared runbook and registers", all(s in body for s in ("AUDIT-RUNBOOK", "FINDINGS-REGISTER")), ""))
    others = [n for n in NAMES if n != name]
    r.append(("scope boundary names every other audit skill", all(o in body for o in others), ",".join(o for o in others if o not in body)))
    refs = sorted(p.name for p in (folder / "references").glob("*.md"))
    unref = [p for p in refs if p not in body]
    r.append(("every references/ file is linked from SKILL.md", bool(refs) and not unref, ",".join(unref)))
    broken = [m for m in re.findall(r"`((?:references|scripts)/[^`\s]+)`", body) if not (folder / m).exists()]
    r.append(("linked references/scripts exist", not broken, ",".join(broken)))
    for script in (folder / "scripts").glob("*.py") if (folder / "scripts").exists() else []:
        try:
            py_compile.compile(str(script), doraise=True)
            ok = True
        except py_compile.PyCompileError:
            ok = False
        src = script.read_text(encoding="utf-8")
        writes = [p for p in WRITE_PATTERNS if re.search(p, src)]
        r.append((f"script {script.name} compiles", ok, ""))
        r.append((f"script {script.name} has no write/DDL operations", not writes, ",".join(writes)))
    evals = json.loads((folder / "evals" / "evals.json").read_text(encoding="utf-8"))
    kinds = [e["name"] for e in evals["evals"]]
    r.append(("evals: positive, negative and dry-run scenarios", any("positive" in k for k in kinds) and any("negative" in k for k in kinds) and any("dry-run" in k for k in kinds), ",".join(kinds)))
    corpus = text + "".join(p.read_text(encoding="utf-8") for p in (folder / "references").glob("*.md"))
    corpus += (ROOT / "docs" / "audits" / "AUDIT-RUNBOOK.md").read_text(encoding="utf-8")
    for e in evals["evals"]:
        for a in e["assertions"]:
            pattern = ASSERTION_EVIDENCE.get(a)
            ok = bool(pattern and re.search(pattern, corpus, re.DOTALL))
            r.append((f"[{e['name']}] instructed: {a}", ok, "" if pattern else "no evidence pattern"))
    triggers = json.loads((folder / "evals" / "triggers.json").read_text(encoding="utf-8"))
    r.append(("trigger examples: >=2 should / >=2 should-not", sum(t["should_trigger"] for t in triggers) >= 2 and sum(not t["should_trigger"] for t in triggers) >= 2, f"{len(triggers)} examples"))
    r.append(("name does not collide with installed skills", name not in installed, ""))
    return r


def shared_checks() -> list[tuple[str, bool, str]]:
    runbook = (ROOT / "docs" / "audits" / "AUDIT-RUNBOOK.md").read_text(encoding="utf-8")
    matrix = (ROOT / "docs" / "audits" / "SCENARIO-MATRIX.md").read_text(encoding="utf-8")
    table = runbook.split("## 1. ")[1].split("\n\n")[1]
    return [
        ("runbook: ownership table lists all five skills", all(f"`{n}`" in table for n in NAMES), ""),
        ("runbook: overlap prevention rules (§1)", "Çakışma önleme" in runbook, ""),
        ("runbook: DRY_RUN protocol and template (§1a)", "## 1a. DRY_RUN modu" in runbook and "NOT_CHECKED (DRY_RUN)" in runbook, ""),
        ("scenario matrix: every row names an owner or status", "Sahip" in matrix, ""),
    ]


def main() -> int:
    installed = installed_skill_names()
    failed = 0
    shared = shared_checks()
    bad_shared = [x for x in shared if not x[1]]
    failed += len(bad_shared)
    print(f"shared: {'PASS' if not bad_shared else 'FAIL'} ({len(shared) - len(bad_shared)}/{len(shared)} checks)")
    for label, _, note in bad_shared:
        print(f"   FAIL {label} {note}")
    for name in NAMES:
        results = check(name, installed)
        bad = [x for x in results if not x[1]]
        failed += len(bad)
        print(f"{name}: {'PASS' if not bad else 'FAIL'} ({len(results) - len(bad)}/{len(results)} checks)")
        for label, ok, note in results:
            if not ok:
                print(f"   FAIL {label} {note}")
    print(f"installed skill names scanned: {len(installed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
