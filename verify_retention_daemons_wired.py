import subprocess
import re
from datetime import datetime, timedelta

def check_cron_jobs():
    """Check if the required daemons are scheduled in cron."""
    cron_patterns = {
        'retention_sweeper.py': r'.*retention_sweeper\.py.*',  # 30-day interval
        'exemption_expirer.py': r'.*exemption_expirer\.py.*',  # Nightly
        'attestation_refresher.py': r'.*attestation_refresher\.py.*'  # Approaching expiry
    }

    try:
        # Get the cron jobs for the current user
        cron_output = subprocess.check_output(['crontab', '-l']).decode('utf-8')
    except subprocess.CalledProcessError:
        cron_output = ""

    missing_daemons = []
    for daemon, pattern in cron_patterns.items():
        if not re.search(pattern, cron_output, re.IGNORECASE):
            missing_daemons.append(daemon)

    return missing_daemons

def check_service_health():
    """Check if heartbeat entries exist in service_health for each daemon."""
    # Placeholder for actual database query
    # This should be replaced with a real query to the service_health table
    # For example, using SQLAlchemy or another ORM
    required_daemons = ['retention_sweeper', 'exemption_expirer', 'attestation_refresher']
    missing_heartbeats = []

    # Simulate a query that checks for recent heartbeats (last 24 hours)
    # In a real implementation, this would query the database
    for daemon in required_daemons:
        # Simulate a missing heartbeat
        if daemon in ['retention_sweeper', 'exemption_expirer']:  # Simulate missing heartbeats for these
            missing_heartbeats.append(daemon)

    return missing_heartbeats

def propose_integration(missing_daemons):
    """Propose integration directives for missing daemons."""
    directives = {
        'retention_sweeper.py': {
            'cron': '0 0 */30 * * /path/to/retention_sweeper.py',
            'description': 'Run every 30 days to expire old evidence'
        },
        'exemption_expirer.py': {
            'cron': '0 0 * * * /path/to/exemption_expirer.py',
            'description': 'Run nightly to expire exemptions'
        },
        'attestation_refresher.py': {
            'cron': '0 0 * * * /path/to/attestation_refresher.py',
            'description': 'Run nightly to refresh attestations approaching expiry'
        }
    }

    return [directives[daemon] for daemon in missing_daemons if daemon in directives]

def main():
    missing_daemons = check_cron_jobs()
    missing_heartbeats = check_service_health()

    if not missing_daemons and not missing_heartbeats:
        print("All daemons are properly wired and have recent heartbeat entries.")
        return

    print("Issues found:")
    if missing_daemons:
        print(f"Missing cron jobs for: {', '.join(missing_daemons)}")
        print("Proposed integration directives:")
        for directive in propose_integration(missing_daemons):
            print(f"  - {directive['cron']}  # {directive['description']}")

    if missing_heartbeats:
        print(f"Missing heartbeat entries for: {', '.join(missing_heartbeats)}")
        print("Please ensure these daemons are running and logging heartbeats.")

if __name__ == "__main__":
    main()