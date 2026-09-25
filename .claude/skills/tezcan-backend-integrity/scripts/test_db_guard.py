"""Read-only pre-flight check before running pytest for an audit.

Refuses (exit 1) when the target test database is shared with a running
sandbox/dev server, already has other sessions, or is the development DB.
Reads only: the Windows process list and pg_stat_activity. Writes nothing.

Usage (PowerShell, project root, same session as the later pytest run):
    $env:POSTGRES_TEST_DB = 'test_tezcan_envanter_audit'
    .venv\\Scripts\\python.exe .claude\\skills\\tezcan-backend-integrity\\scripts\\test_db_guard.py --db $env:POSTGRES_TEST_DB

POSTGRES_TEST_DB must be set in the session and equal --db, so the database
checked here is the one pytest will use. Connection settings come from the
POSTGRES_* environment variables; the password is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SANDBOX_SETTINGS_MARKERS = ("pilot_settings",)
SANDBOX_DB = "test_tezcan_envanter"  # used by the :8001 sandbox (pilot_settings)


def running_python_commands() -> list[str]:
    if os.name != "nt":
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, check=False)
        return [line for line in out.stdout.splitlines() if "python" in line]
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object -ExpandProperty CommandLine | ConvertTo-Json"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        data = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(data, str):
        data = [data]
    return [line for line in data if line]


def other_sessions(db_name: str) -> tuple[int | None, str]:
    try:
        import psycopg
    except ImportError:
        return None, "psycopg not importable (run with the project .venv python)"
    try:
        with psycopg.connect(
            dbname=os.environ.get("POSTGRES_DB", "tezcan_envanter"),
            user=os.environ.get("POSTGRES_USER", "tezcan_envanter"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
            connect_timeout=5,
        ) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                if cur.fetchone() is None:
                    return None, "database does not exist (create it first; see FINDINGS-REGISTER ENV-001)"
                cur.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                    (db_name,),
                )
                return cur.fetchone()[0], "ok"
    except Exception as exc:  # report the class only; messages may echo connection details
        return None, f"could not query pg_stat_activity ({exc.__class__.__name__})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="target pytest database name (POSTGRES_TEST_DB)")
    args = parser.parse_args()
    target = args.db
    problems: list[str] = []
    notes: list[str] = []

    session_test_db = os.environ.get("POSTGRES_TEST_DB", "")
    if not session_test_db:
        problems.append("POSTGRES_TEST_DB is not set in this session (pytest would use the settings default)")
    elif session_test_db != target:
        problems.append(f"POSTGRES_TEST_DB ('{session_test_db}') differs from --db ('{target}')")

    dev_db = os.environ.get("POSTGRES_DB", "tezcan_envanter")
    if target == dev_db:
        problems.append(f"target '{target}' is the development database")
    if not target.startswith("test_"):
        problems.append("target name must start with 'test_' (Django test DB convention)")

    commands = running_python_commands()
    sandbox_running = any(m in c for c in commands for m in SANDBOX_SETTINGS_MARKERS)
    pytest_running = [c for c in commands if "pytest" in c]
    if sandbox_running and target == SANDBOX_DB:
        problems.append(f"sandbox (pilot_settings) is running and uses '{SANDBOX_DB}'")
    if pytest_running:
        problems.append(f"{len(pytest_running)} pytest process(es) already running")
    notes.append(f"sandbox running: {sandbox_running}")

    sessions, status = other_sessions(target)
    if sessions is None:
        problems.append(f"session check: {status}")
    elif sessions > 0:
        problems.append(f"{sessions} other session(s) connected to '{target}'")
    else:
        notes.append(f"no other sessions on '{target}'")

    verdict = "FAIL" if problems else "PASS"
    print(f"test_db_guard: {verdict} (target={target})")
    for p in problems:
        print(f"  problem: {p}")
    for n in notes:
        print(f"  note: {n}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
