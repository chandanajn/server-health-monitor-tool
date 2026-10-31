# Server Health Monitor API

A FastAPI-based application to monitor server log files across different environments, identify active faults, and provide server health information through REST APIs.


## Development 

### 1. Repository Setup
Created the Git repository and set up the initial project configuration. Added `.gitignore` to exclude virtual environments, Python cache files, and local state files. Also configured Git to handle line endings consistently.

### 2. Project Structure and Dependencies
Set up the project using a `src` directory structure and configured dependencies in `pyproject.toml`. Added separate development dependencies and lockfiles.

Created sample log directories for `dev`, `prd`, and `staging` environments. Added sample server logs to test the monitoring functionality.

Verified the project installation and ran the initial code checks using Ruff, Ruff formatter, and MyPy. All checks passed.

### 3. Domain Models and Log Parser
Added the initial models required for monitoring, including severity levels, log events, faults, file checkpoints, server status, and scan reports.

Implemented the log parser to read server log entries and identify their severity and details. Added support for resolving faults through explicit `INFO RESOLVED` log entries.

The parser also handles invalid log entries without stopping the application. Added basic validation for timestamps, resolution entries, and input length. Invalid entries are handled safely, with a limited preview retained for debugging.

Added unit tests to cover different severity levels, timestamp formats, fault resolution entries, invalid inputs, and boundary cases.

### 4. Checkpoint Store
Implemented state persistence using an atomic write mechanism. File offsets and active faults are saved together to a temporary file, flushed with fsync, and swapped in place using an atomic replace operation to prevent partial or corrupted writes during unexpected shutdowns.

Added strict schema validation on load to ensure file paths are safe relative paths and fault IDs are consistent. If a state file is found to be malformed or corrupted, it is automatically moved to a timestamped quarantine file rather than crashing the service, allowing monitoring to continue safely with a fresh state.

Added unit tests to verify save and reload roundtrips, unchanged state write avoidance, corrupt file quarantine handling, schema version checks, and path traversal protection.

### 5. Monitor Engine
Built the core log monitoring engine that traverses the log directory tree and tracks active server faults. It scans files incrementally, seeking to saved byte offsets rather than reading full log files repeatedly.

Added file rotation and truncation detection using inode and file size tracking. If a log file shrinks or changes inode, the offset resets to the beginning while retaining any existing active faults. Incomplete trailing lines without newlines are held back until the next scan cycle. If an error occurs on a single file, it is isolated and recorded without interrupting the scan for other servers.

Added unit tests covering baseline metrics against sample logs (6 servers, 3 faulty at ERROR, 4 faulty at WARN), incremental appends, partial-line buffering, log rotations, vanished files, and the full fault resolution lifecycle.

### 6. Settings and Logging
Implemented environment-based configuration using Pydantic Settings with the HM_ prefix. Added validation rules ensuring the root log directory exists, the state file path is located outside the monitored root directory, the scan interval is at least 0.1 seconds, and the optional API key has a minimum length of 16 characters.

Configured application logging supporting both human-readable text and structured single-line JSON formatting. Added context variable tracking so request identifiers can be propagated to all log records for end-to-end tracing.

Added unit tests to verify default configuration values, environment variable overrides, .env file loading with environment precedence, validation failure cases, and structured JSON log formatting.

### 7. Application Factory and Health Endpoints
Created the FastAPI application factory using an asynchronous lifespan context manager. On startup, the service runs an initial scan and starts a background monitoring task that periodically rescans the log tree. On shutdown, it stops the background task cleanly and saves state to disk.

Built the service coordinator layer with thread-safe locking to keep scan cycles and file persistence synchronized. Added an in-memory snapshot cache so read queries can be answered instantly without accessing the disk.

Implemented health check probe endpoints: `/healthz` for process liveness and `/readyz` for readiness, verifying that the initial scan has completed and that the most recent scan is not stale. Added unit tests for health endpoints, lifespan startup/shutdown, and stale state detection.

### 8. Read REST API Endpoints
Defined Pydantic schemas and implemented read endpoints under `/api/v1` for operational reporting, server queries, and active faults. All read operations serve directly from the in-memory snapshot without touching disk files.

Added `/api/v1/report` to return total and faulty server counts along with affected servers ordered by highest severity. Added `/api/v1/servers` with pagination (`limit` and `offset`) and filtering by environment and operational status (`healthy` or `faulty`). Added `/api/v1/servers/{env}/{server}` to return individual server details and active faults, validating paths against discovered servers to prevent path traversal. Added `/api/v1/faults` providing a flat list of active faults with optional severity and environment filtering.

Added untrusted text sanitization to strip non-printable control characters from log messages before returning them in API responses. Added unit tests for baseline report metrics, pagination boundaries, query parameter validation, and 404 responses for nonexistent servers.

### 9. API Authentication and Fault Resolution
Implemented authentication and write operations under `/api/v1`. Added API key verification using constant-time string comparison (`hmac.compare_digest`) via the `X-API-Key` header. If no API key is configured on the service, write routes return 403 `writes_disabled`. Missing or invalid keys return 401.

Added `POST /api/v1/scan` to allow authorized clients to trigger an immediate scan cycle under the service lock.

Added `POST /api/v1/servers/{env}/{server}/faults/{fault_id}/resolve` to resolve active faults. The endpoint appends an explicit `INFO RESOLVED` record directly to the server's own log file using an atomic binary append write with leading newline protection. After appending, an immediate rescan runs to clear the active fault and update the persisted checkpoint state. Attempting to resolve an already-cleared fault returns 409 `fault_not_active`, while unknown servers or faults return 404.

Added unit tests covering authentication states, manual scans, fault resolution lifecycles, newline handling, double resolution prevention, and path validation safety.

### 10. Error Handling, Request ID, and Metrics
Added a unified JSON error envelope containing machine-readable error codes, messages, and request identifiers across all HTTP error statuses (400, 401, 403, 404, 405, 409, 422, and 500). Unhandled exceptions log full tracebacks with request IDs and return generic safe messages without leaking internal application details.

Implemented HTTP middleware to accept or generate `X-Request-ID` headers, bind them to context variables for enriched logging, attach them to all responses, and output single-line access logs with method, path, status, and duration in milliseconds.

Added Prometheus metrics collection and a `/metrics` export endpoint supporting gauges for total and faulty servers, scan cycle duration histograms, and counters for scan failures and resolved faults. Metrics export can be toggled via `HM_ENABLE_METRICS`, returning 404 with standard error format when disabled.

Added unit tests covering error response envelopes across all status codes, request ID reuse, unhandled exception masking, Prometheus metrics values, and disabled metrics handling.

### 11. Test Hardening and Edge Case Coverage
Added end-to-end integration tests to verify the complete operational cycle: incremental log append detection, fault reflection in report and server detail endpoints, fault resolution via manual log file editing, live file rotation and truncation handling, and state preservation across service restarts.

Extended test coverage across edge cases, including Windows CRLF line endings, UTF-8 byte-order marks (BOM), invalid byte sequences, high-volume directory trees with 500 server log files, empty directory monitoring, directory deletion during runtime while retaining unresolved faults, and corrupt checkpoint quarantine recovery at startup.

Configured automated test coverage measurement producing detailed terminal output and `coverage.xml` reports, achieving 90.47% test coverage across the application codebase against the 80% coverage requirement.

### 12. Development Commands and Strict Static Analysis
Configured strict MyPy type checking for the core application codebase in `pyproject.toml`, disallowing untyped definitions, incomplete definitions, and untyped decorators. Verified code cleanliness with zero Ruff linting issues and zero formatting deviations.

Audited project dependencies using `pip-audit`, confirming zero known security vulnerabilities in both production and development packages.

The primary development commands used across the project:

- **Linting**:
  ```powershell
  ruff check .
  ```
- **Formatting check**:
  ```powershell
  ruff format --check .
  ```
- **Code formatting**:
  ```powershell
  ruff format .
  ```
- **Type checking (strict)**:
  ```powershell
  mypy src tests
  ```
- **Test execution with coverage**:
  ```powershell
  pytest --cov=health_api --cov-report=term-missing --cov-report=xml --cov-fail-under=80
  ```
- **Dependency vulnerability audit**:
  ```powershell
  pip-audit -r requirements.txt
  ```
- **Run service locally**:
  ```powershell
  python -m health_api
  ```

### 13. Containerization and Automated Smoke Testing
Created a production-ready, multi-stage `Dockerfile` using `python:3.11-slim`:
- **Builder stage**: Compiles dependencies into an isolated virtual environment (`/opt/venv`).
- **Runtime stage**: Runs exclusively as a dedicated non-root user (`appuser`, fixed UID/GID 10001), declares mount targets `/data/org` (monitored logs) and `/data/state` (checkpoint state), and configures container health checks using standard library `urllib`.
- **Packaging safety**: Configured `.dockerignore` to prevent staging repository history, development tools, test files, and local state into the image.

Implemented a dependency-free container verification script in `scripts/smoke_test.py` covering six core scenarios:
1. **Scenario A**: Baseline report verification (6 monitored servers, 3 faulty).
2. **Scenario B**: Write authentication (401/403 without API key, 200 with API key, faulty server count drops to 2).
3. **Scenario C**: Consistent JSON error envelope for unknown resources (404).
4. **Scenario D**: Verification that the container process runs as UID 10001.
5. **Scenario E**: Checkpoint state persistence across container restarts.
6. **Scenario F**: Fast container termination and non-zero exit on invalid configuration.

Container commands:
- **Build image**:
  ```powershell
  docker build -t health-api:local .
  ```
- **Run container smoke test**:
  ```powershell
  python scripts/smoke_test.py health-api:local
  ```
- **Run container with volume mounts**:
  ```powershell
  docker run --rm -p 8000:8000 -v "${PWD}\org:/data/org:ro" health-api:local
  ```

### 14. Continuous Integration Pipeline (Lint, Type Check, and Test)
Created the initial GitHub Actions workflow in `.github/workflows/ci.yml` triggered on pushes to `main`, tags matching `v*`, and pull requests targeting `main`:
- **Security & Concurrency**: Restricts permissions to read-only (`contents: read`) at the workflow root and uses concurrency cancellation for superseded pull request runs.
- **Stage 1 (Lint)**: Sets up Python 3.11 with pip package caching, installs dependencies, and runs `ruff check .`, `ruff format --check .`, strict `mypy src tests scripts`, and `pip-audit -r requirements.txt`.
- **Stage 2 (Test)**: Depends on the lint stage, executes the full pytest suite with branch coverage validation (`--cov-fail-under=80`), and uploads `coverage.xml` as an automated workflow artifact.
