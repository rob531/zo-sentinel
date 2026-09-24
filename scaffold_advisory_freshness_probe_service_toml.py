import os

SERVICE_NAME = "advisory_freshness_probe"
OUTPUT_PATH = f"/home/workspace/zo_sentinel/{SERVICE_NAME}_service_toml.py"

TOML_CONTENT = r"""[project]
name = "advisory_freshness_probe"
version = "1.0.0"
description = "Sentinel advisory freshness probe — monitors threat-intel feed staleness"

[project.scripts]
advisory-freshness-probe = "advisory_freshness_probe_service:main"

[tool.supervisord]
program.name = "advisory_freshness_probe"
program.command = "{PYTHON} /home/workspace/zo_sentinel/advisory_freshness_probe.py"
program.directory = "/home/workspace/zo_sentinel"
program.stdout_logfile = "/home/workspace/logs/advisory_freshness_probe.log"
program.stderr_logfile = "/home/workspace/logs/advisory_freshness_probe.err.log"
program.autostart = true
program.autorestart = true
program.startretries = 3
program.exitcodes = [0, 2]

[tool.zo-sentinel]
service_type = "daemon"
poll_seconds = 3600
health_endpoint = "http://127.0.0.1:8791/health"
write_service_url = "http://127.0.0.1:8772"
port = 8791
pid_file = "/tmp/advisory_freshness_probe.pid"

[tool.zo-sentinel.tables]
query = ["threat_intel_feeds", "service_health", "mcp_threat_associations"]

[tool.zo-sentinel.alerts]
stale_threshold_hours = 48
critical_threshold_hours = 168
"""

def write_toml_scaffold():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(TOML_CONTENT)
    print(f"Wrote: {OUTPUT_PATH}")


def main():
    write_toml_scaffold()
    print("Scaffold complete.")


if __name__ == "__main__":
    main()