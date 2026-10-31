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

