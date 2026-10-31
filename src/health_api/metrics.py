"""Prometheus metrics export and recording utilities."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

servers_total = Gauge("hm_servers_total", "Total number of monitored servers")
servers_faulty = Gauge("hm_servers_faulty", "Number of servers with active faults")
scan_duration_seconds = Histogram(
    "hm_scan_duration_seconds",
    "Duration of scan cycles in seconds",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
scan_failures_total = Counter("hm_scan_failures_total", "Total number of failed scan cycles")
faults_resolved_total = Counter(
    "hm_faults_resolved_total", "Total number of faults successfully resolved"
)


def update_scan_metrics(total: int, faulty: int, duration_seconds: float) -> None:
    """Record successful scan results."""
    servers_total.set(total)
    servers_faulty.set(faulty)
    scan_duration_seconds.observe(duration_seconds)


def record_scan_failure() -> None:
    """Increment failed scan counter."""
    scan_failures_total.inc()


def record_fault_resolved() -> None:
    """Increment resolved faults counter."""
    faults_resolved_total.inc()
