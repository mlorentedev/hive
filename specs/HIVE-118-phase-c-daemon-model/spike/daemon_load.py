"""HIVE-118 Phase C — REAL-daemon load / bug-hunt harness.

Unlike ``load_spike.py`` (which stressed a *toy* FastMCP server to de-risk the
concurrency model before any code existed), this drives the **real** ``hive
serve`` daemon end to end through its production tool surface — the vault + git
write path, the metrics core, and the ``/status`` endpoint — under N concurrent
sessions doing a mixed read/write workload. The goal is to surface bugs the
unit/integration tests cannot: serialization failures under contention, git
corruption / stale ``index.lock``, lost metric increments, fd/connection leaks,
head-of-line blocking, and the proxied latency tail (the deferred M2 question).

Run (knobs via argv; defaults 12 / 30 / 0.5)::

    uv run python specs/HIVE-118-phase-c-daemon-model/spike/daemon_load.py \
        [sessions] [calls_per_session] [write_ratio]

Exit ``0`` = every check passed. Linux/macOS: reports daemon fd + RSS deltas
(psutil); Windows: fd check is skipped (num_fds unsupported there).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil

HOST = "127.0.0.1"

N_SESSIONS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 12
CALLS = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 30
WRITE_RATIO = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5


# ── vault + daemon setup ───────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, deadline_s: float = 20.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        with contextlib.suppress(OSError), socket.create_connection(
            (HOST, port), timeout=0.5,
        ):
            return True
        time.sleep(0.1)
    return False


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
    )


def _setup_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "10_projects" / "demo").mkdir(parents=True)
    (vault / "10_projects" / "demo" / "00-context.md").write_text(
        "---\ntitle: demo\n---\nBASE\n", encoding="utf-8",
    )
    _git(["init"], vault)
    _git(["config", "user.email", "load@test.com"], vault)
    _git(["config", "user.name", "Load"], vault)
    _git(["add", "."], vault)
    _git(["commit", "-m", "init"], vault)
    return vault


def _env(root: Path, vault: Path) -> dict[str, str]:
    return {
        **os.environ,
        "HIVE_DB_PATH": str(root / "worker.db"),
        "HIVE_RELEVANCE_DB_PATH": str(root / "relevance.db"),
        "HIVE_LESSON_DB_PATH": str(root / "lesson.db"),
        "HIVE_LOG_PATH": str(root / "hive.log"),
        "VAULT_PATH": str(vault),
    }


# ── client workload ────────────────────────────────────────────────────────


async def _session(url: str, token: str, sid: int) -> dict[str, object]:
    """One MCP session: CALLS mixed read/write ops. Returns per-session stats."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        url, headers={"Authorization": f"Bearer {token}"},
    )
    reads = writes = errors = 0
    markers: list[str] = []
    latencies: list[float] = []
    # Deterministic per-session read/write schedule (no global RNG — keeps the
    # run reproducible and the expected write count exact).
    async with Client(transport) as client:
        for n in range(CALLS):
            is_write = ((n * 7 + sid) % 100) / 100.0 < WRITE_RATIO
            t0 = time.monotonic()
            try:
                if is_write:
                    marker = f"s{sid}-w{n}"
                    await client.call_tool("vault_write", {
                        "project": "demo", "section": "context",
                        "operation": "append", "content": f"\n{marker}\n",
                    })
                    markers.append(marker)
                    writes += 1
                else:
                    await client.call_tool("vault_query", {
                        "project": "demo", "section": "context",
                    })
                    reads += 1
            except Exception:  # noqa: BLE001 — load harness tallies any failure
                errors += 1
            latencies.append((time.monotonic() - t0) * 1000)
    return {"reads": reads, "writes": writes, "errors": errors,
            "markers": markers, "latencies": latencies}


async def _run_load(url: str, token: str) -> list[dict[str, object]]:
    return list(await asyncio.gather(
        *(_session(url, token, i) for i in range(N_SESSIONS)),
    ))


# ── status probe ───────────────────────────────────────────────────────────


def _get_status(port: int, token: str) -> dict[str, object]:
    import httpx

    resp = httpx.get(
        f"http://{HOST}:{port}/status",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return dict(resp.json())


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


# ── driver ─────────────────────────────────────────────────────────────────


def main() -> int:  # noqa: PLR0915 — linear harness, readability over splitting
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="hive-load-"))
    vault = _setup_vault(root)
    env = _env(root, vault)
    port = _free_port()
    url = f"http://{HOST}:{port}/mcp"
    checks: list[tuple[str, bool, str]] = []

    daemon = subprocess.Popen(  # noqa: S603 — fixed argv
        [sys.executable, "-m", "hive.server", "serve", "--port", str(port)],
        env=env,
    )
    try:
        if not _wait_ready(port):
            print("daemon did not start")
            return 1
        token = (root / "daemon.token").read_text(encoding="utf-8").strip()
        proc = psutil.Process(daemon.pid)
        fds_before = _num_fds(proc)

        wall0 = time.monotonic()
        results = asyncio.run(_run_load(url, token))
        wall_s = time.monotonic() - wall0

        # Let any in-flight fd churn settle, then snapshot the daemon again.
        time.sleep(1.0)
        fds_after = _num_fds(proc)
        rss_mb = proc.memory_info().rss / 1_000_000

        reads = sum(int(r["reads"]) for r in results)
        writes = sum(int(r["writes"]) for r in results)
        errors = sum(int(r["errors"]) for r in results)
        all_markers = [m for r in results for m in r["markers"]]  # type: ignore[union-attr]
        lats = [x for r in results for x in r["latencies"]]  # type: ignore[union-attr]
        total = reads + writes

        # 1. Zero failures across every session.
        checks.append((
            f"{N_SESSIONS}x{CALLS} mixed ops: zero failures",
            errors == 0, f"ok={total}, errors={errors}",
        ))

        # 2. No lost writes — every appended marker survived (single owner
        #    serialized the concurrent read-append-write cycles).
        content = (vault / "10_projects" / "demo" / "00-context.md").read_text(
            encoding="utf-8",
        )
        missing = [m for m in all_markers if m not in content]
        checks.append((
            "no lost writes (single-owner serialization)",
            not missing, f"writes={writes}, missing={len(missing)}",
        ))

        # 3. Git integrity — fsck clean, one commit per write, no stale lock.
        fsck = _git(["fsck", "--full", "--strict"], vault)
        commits = len(_git(["log", "--oneline"], vault).stdout.splitlines())
        stale_lock = (vault / ".git" / "index.lock").exists()
        git_ok = fsck.returncode == 0 and commits == 1 + writes and not stale_lock
        checks.append((
            "git integrity (fsck + commit count + no stale lock)",
            git_ok,
            f"fsck={fsck.returncode} commits={commits}/{1 + writes} "
            f"stale_lock={stale_lock}",
        ))

        # 4. Metrics accuracy — /status counts exactly what was executed.
        status = _get_status(port, token)
        tools = dict(status["tools"])  # type: ignore[arg-type]
        m_writes = int(tools.get("vault_write", {}).get("calls", 0))  # type: ignore[union-attr]
        m_reads = int(tools.get("vault_query", {}).get("calls", 0))  # type: ignore[union-attr]
        m_total = int(status["total_calls"])
        m_sessions = int(status["sessions_started"])
        metrics_ok = (
            m_writes == writes and m_reads == reads
            and m_total == total and m_sessions >= N_SESSIONS
        )
        checks.append((
            "metrics accuracy (/status == actual, no lost increments)",
            metrics_ok,
            f"writes={m_writes}/{writes} reads={m_reads}/{reads} "
            f"total={m_total}/{total} sessions={m_sessions}/{N_SESSIONS}",
        ))

        # 5. No fd/connection leak on the daemon (skipped on Windows).
        if fds_before >= 0 and fds_after >= 0:
            leak = fds_after - fds_before
            checks.append((
                "no daemon fd leak (post-load ~= pre-load)",
                leak <= 5, f"fds {fds_before}->{fds_after} (Δ{leak}), rss={rss_mb:.0f}MB",
            ))
        else:
            checks.append(("daemon fd leak", True, "skipped (no num_fds on this OS)"))

        # 6. Latency tail (proxied path, the deferred M2 measurement).
        thr = total / wall_s if wall_s else 0
        checks.append((
            "latency tail bounded",
            _pct(lats, 99) < 10_000,
            f"p50={_pct(lats, 50):.0f} p95={_pct(lats, 95):.0f} "
            f"p99={_pct(lats, 99):.0f} ms | {thr:.0f} ops/s over {wall_s:.1f}s",
        ))
    finally:
        daemon.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            daemon.wait(timeout=10)
        if daemon.poll() is None:
            daemon.kill()

    return _report(checks, root)


def _num_fds(proc: psutil.Process) -> int:
    """Open fd count, or -1 where the platform does not support it (Windows)."""
    with contextlib.suppress(Exception):
        return int(proc.num_fds())
    return -1


def _report(checks: list[tuple[str, bool, str]], root: Path) -> int:
    print(f"\nHIVE-118 daemon load — {N_SESSIONS} sessions x {CALLS} calls "
          f"(write_ratio={WRITE_RATIO})\n")
    width = max(len(name) for name, _, _ in checks)
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
    print(f"\n{'ALL PASS' if all_ok else 'FAILED'} — "
          f"{sum(ok for _, ok, _ in checks)}/{len(checks)} checks\n")
    with contextlib.suppress(Exception):
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
