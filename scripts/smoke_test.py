#!/usr/bin/env python3
"""Local and CI container smoke test suite using only standard library modules.

Verifies container readiness, non-root user execution, API responses, fault resolution,
state persistence, and invalid configuration termination.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_KEY = "test-smoke-secret-key-16-chars"


def find_free_port() -> int:
    """Find and return an unused ephemeral TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_ready(port: int, timeout_seconds: float = 30.0) -> bool:
    """Poll /readyz until 200 OK or timeout reached."""
    url = f"http://127.0.0.1:{port}/readyz"
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, Any]:
    """Execute an HTTP request and return (status_code, parsed_json_or_text)."""
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def main() -> int:
    image_tag = sys.argv[1] if len(sys.argv) > 1 else "health-api:local"
    print(f"=== Starting container smoke test for image: {image_tag} ===")

    temp_root = Path(tempfile.mkdtemp(prefix="smoke_test_"))
    temp_org = temp_root / "org"
    temp_state = temp_root / "state"

    # Copy sample logs to test environment
    source_org = Path("org").resolve()
    if not source_org.exists():
        print(f"ERROR: sample data directory '{source_org}' not found.")
        return 1

    shutil.copytree(source_org, temp_org)
    temp_state.mkdir(parents=True, exist_ok=True)

    # Make state directory world-writable so UID 10001 inside container can write checkpoints
    with contextlib.suppress(Exception):
        os.chmod(temp_state, 0o777)

    container_main = f"smoke_test_main_{int(time.time())}"
    container_restart = f"smoke_test_restart_{int(time.time())}"
    port = find_free_port()

    passed_scenarios: list[str] = []
    failed_scenarios: list[str] = []

    def record_pass(name: str, detail: str = "") -> None:
        detail_msg = f" - {detail}" if detail else ""
        print(f"[PASS] {name}{detail_msg}")
        passed_scenarios.append(name)

    def record_fail(name: str, reason: str) -> None:
        print(f"[FAIL] {name}: {reason}")
        failed_scenarios.append(name)

    try:
        # Run main container
        cmd_run = [
            "docker",
            "run",
            "-d",
            "--name",
            container_main,
            "-p",
            f"{port}:8000",
            "-v",
            f"{temp_org.resolve()}:/data/org:rw",
            "-v",
            f"{temp_state.resolve()}:/data/state:rw",
            "-e",
            f"HM_API_KEY={API_KEY}",
            image_tag,
        ]
        proc = subprocess.run(cmd_run, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"ERROR: failed to start container: {proc.stderr}")
            return 1

        print(f"Container {container_main} started on port {port}. Waiting for readiness...")
        if not wait_for_ready(port, timeout_seconds=30.0):
            logs = subprocess.run(
                ["docker", "logs", container_main], capture_output=True, text=True
            ).stdout
            print(f"ERROR: container failed to become ready within 30s. Logs:\n{logs}")
            return 1

        base_url = f"http://127.0.0.1:{port}"

        # Scenario A: Initial baseline report metrics
        status_code, data = http_request(f"{base_url}/api/v1/report")
        if (
            status_code == 200
            and data.get("total_servers") == 6
            and data.get("faulty_servers") == 3
        ):
            record_pass("Scenario A", "Report reflects 6 servers, 3 faulty")
        else:
            record_fail(
                "Scenario A", f"Expected 6 servers and 3 faulty, got status {status_code}: {data}"
            )

        # Scenario B: Authentication and Fault Resolution
        resolve_url = f"{base_url}/api/v1/servers/dev/server-2/faults/F001/resolve"
        code_unauth, _ = http_request(resolve_url, method="POST")
        if code_unauth in (401, 403):
            code_auth, _ = http_request(
                resolve_url,
                method="POST",
                headers={"X-API-Key": API_KEY},
            )
            if code_auth == 200:
                _, rep_data = http_request(f"{base_url}/api/v1/report")
                if rep_data.get("faulty_servers") == 2:
                    record_pass(
                        "Scenario B", "Fault resolved with API key; faulty count dropped to 2"
                    )
                else:
                    record_fail(
                        "Scenario B", f"Expected 2 faulty servers after resolution, got {rep_data}"
                    )
            else:
                record_fail("Scenario B", f"Resolution with API key returned status {code_auth}")
        else:
            record_fail("Scenario B", f"Expected 401/403 without API key, got {code_unauth}")

        # Scenario C: Unknown server returns standard 404 envelope
        code_404, err_body = http_request(f"{base_url}/api/v1/servers/dev/nonexistent")
        if (
            code_404 == 404
            and isinstance(err_body, dict)
            and "error" in err_body
            and err_body["error"].get("code") == "server_not_found"
            and "request_id" in err_body["error"]
        ):
            record_pass("Scenario C", "Unknown server returns unified error envelope")
        else:
            record_fail("Scenario C", f"Expected unified 404 envelope, got {code_404}: {err_body}")

        # Scenario D: Container runs as non-root UID 10001
        proc_uid = subprocess.run(
            ["docker", "exec", container_main, "id", "-u"],
            capture_output=True,
            text=True,
        )
        container_uid = proc_uid.stdout.strip()
        if proc_uid.returncode == 0 and container_uid == "10001":
            record_pass("Scenario D", f"Container executes as UID {container_uid}")
        else:
            record_fail(
                "Scenario D",
                f"Expected UID 10001, got code {proc_uid.returncode}, uid: {container_uid}",
            )

        # Scenario E: Checkpoint persistence across container restarts
        subprocess.run(["docker", "stop", container_main], capture_output=True, text=True)
        subprocess.run(["docker", "rm", "-f", container_main], capture_output=True, text=True)

        restart_port = find_free_port()
        cmd_restart = [
            "docker",
            "run",
            "-d",
            "--name",
            container_restart,
            "-p",
            f"{restart_port}:8000",
            "-v",
            f"{temp_org.resolve()}:/data/org:ro",
            "-v",
            f"{temp_state.resolve()}:/data/state:rw",
            "-e",
            f"HM_API_KEY={API_KEY}",
            image_tag,
        ]
        proc_re = subprocess.run(cmd_restart, capture_output=True, text=True)
        if proc_re.returncode == 0 and wait_for_ready(restart_port, timeout_seconds=30.0):
            _, rep_restarted = http_request(f"http://127.0.0.1:{restart_port}/api/v1/report")
            if rep_restarted.get("faulty_servers") == 2:
                record_pass(
                    "Scenario E",
                    "Checkpoint state retained across container restart (2 faulty servers)",
                )
            else:
                record_fail(
                    "Scenario E", f"Expected 2 faulty servers after restart, got {rep_restarted}"
                )
        else:
            record_fail("Scenario E", "Restarted container failed to start or become ready")

        # Scenario F: Invalid configuration terminates quickly with non-zero exit
        cmd_invalid = [
            "docker",
            "run",
            "--rm",
            "-e",
            "HM_MIN_SEVERITY=bogus_value",
            image_tag,
        ]
        proc_inv = subprocess.run(cmd_invalid, capture_output=True, text=True)
        if proc_inv.returncode != 0:
            record_pass(
                "Scenario F", "Invalid HM_MIN_SEVERITY terminates container with non-zero exit code"
            )
        else:
            record_fail("Scenario F", "Expected non-zero exit code on invalid configuration")

    finally:
        # Cleanup containers
        subprocess.run(["docker", "rm", "-f", container_main], capture_output=True, text=True)
        subprocess.run(["docker", "rm", "-f", container_restart], capture_output=True, text=True)

        # Cleanup temporary test directories
        with contextlib.suppress(Exception):
            shutil.rmtree(temp_root)

    print("\n=== Smoke Test Summary ===")
    print(f"Total Scenarios: {len(passed_scenarios) + len(failed_scenarios)}")
    print(f"Passed: {len(passed_scenarios)}")
    print(f"Failed: {len(failed_scenarios)}")

    return 0 if not failed_scenarios else 1


if __name__ == "__main__":
    sys.exit(main())
