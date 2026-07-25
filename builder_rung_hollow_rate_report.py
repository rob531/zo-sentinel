import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List
from app.db import get_session
from app.models import Directive, AlreadyBuiltModule

def get_directive_outcomes(session) -> Dict[str, bool]:
    """Query the database for directive outcomes."""
    directives = session.query(Directive).all()
    built_modules = {module.name for module in session.query(AlreadyBuiltModule).all()}

    outcomes = {}
    for directive in directives:
        outcomes[directive.name] = directive.name in built_modules

    return outcomes

def get_proposed_directives() -> List[str]:
    """Read proposed directives from the filesystem."""
    proposed_dir = Path("directives/proposed")
    return [f.stem for f in proposed_dir.glob("*.py")]

def compute_hollow_rate(proposed: List[str], outcomes: Dict[str, bool]) -> Dict:
    """Compute the hollow rung rate and related metrics."""
    total_proposed = len(proposed)
    total_built = sum(outcomes.values())

    proposals_without_builds = [d for d in proposed if not outcomes.get(d, False)]
    built_without_proposal = [d for d, built in outcomes.items() if built and d not in proposed]

    hollow_rate = (len(proposals_without_builds) / total_proposed) if total_proposed > 0 else 0.0

    return {
        "total_proposed": total_proposed,
        "total_built": total_built,
        "hollow_rate": hollow_rate,
        "proposals_without_builds": proposals_without_builds,
        "built_without_proposal": built_without_proposal,
        "generated_at": datetime.now().isoformat()
    }

def generate_report() -> Dict:
    """Generate the hollow rung rate report."""
    session = get_session()
    outcomes = get_directive_outcomes(session)
    proposed = get_proposed_directives()
    report = compute_hollow_rate(proposed, outcomes)
    session.close()
    return report

def print_report_table(report: Dict):
    """Print the report in a table format."""
    print(f"{'Metric':<30} | {'Value':<20}")
    print("-" * 50)
    print(f"{'Total Proposed':<30} | {report['total_proposed']:<20}")
    print(f"{'Total Built':<30} | {report['total_built']:<20}")
    print(f"{'Hollow Rate':<30} | {report['hollow_rate']:.2f}")
    print(f"{'Proposals Without Builds':<30} | {len(report['proposals_without_builds']):<20}")
    print(f"{'Built Without Proposal':<30} | {len(report['built_without_proposal']):<20}")

if __name__ == "__main__":
    report = generate_report()
    print_report_table(report)
    assert 0.0 <= report["hollow_rate"] <= 1.0, "Hollow rate must be between 0.0 and 1.0"
    print("PASS")