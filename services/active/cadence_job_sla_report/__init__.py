"""services.active.cadence_job_sla_report -- package marker.

GR-29 (2026-11-27): this file used to hold a second, never-mounted copy of builder
output that POSTed SQL to the factory write_service at 127.0.0.1:8772 -- a bus that is
not in the Fly image. The spine mounts `cadence_job_sla_report.router` (service.toml import_path), so
nothing here was reachable, but it still ran at import and shipped plane-confused code.
The live endpoint is router.py. Keep this file a marker:
tools/fly_bus_plane_check.py fails CI if shipped code addresses :8772 again.
"""
