import sys
from collections import defaultdict

REMEDIES = {
    "path_drift": {
        "root": "Path drift detected in data pipeline",
        "fix": "Realign data paths and update configuration",
        "prior_refs": [123, 456]
    },
    "novelty_starvation": {
        "root": "System not receiving novel data inputs",
        "fix": "Check data sources and ensure fresh data ingestion",
        "prior_refs": [789]
    },
    "capacity_429": {
        "root": "Rate limiting or capacity issues",
        "fix": "Increase capacity or adjust rate limits",
        "prior_refs": [101, 112]
    },
    "key_hydration": {
        "root": "Missing or invalid API keys",
        "fix": "Rehydrate keys and validate configurations",
        "prior_refs": [131]
    },
    "dup_poison": {
        "root": "Duplicate data poisoning the system",
        "fix": "Implement deduplication logic",
        "prior_refs": [141, 152]
    },
    "publisher_noop_cap": {
        "root": "Publisher not operational or capped",
        "fix": "Restart publisher or increase capacity",
        "prior_refs": [161]
    },
    "ghost_build": {
        "root": "Build artifacts not properly cleaned up",
        "fix": "Clean up build artifacts and restart",
        "prior_refs": [171]
    },
    "write_service": {
        "root": "Write service failure",
        "fix": "Restart write service and check dependencies",
        "prior_refs": [181]
    },
    "bootstrap_service": {
        "root": "Bootstrap service failure",
        "fix": "Restart bootstrap service and validate configuration",
        "prior_refs": [191]
    },
    "shim_5xx": {
        "root": "Shim layer returning server errors",
        "fix": "Debug shim layer and restart",
        "prior_refs": [201]
    }
}

def remedy(class_name):
    return REMEDIES.get(class_name, {
        "root": "Unknown failure class",
        "fix": "Investigate and diagnose",
        "prior_refs": []
    })

def classify_line(line):
    # Placeholder for the actual classification logic from failure_classifier.py
    # This should be replaced with the actual import and usage of classify_line
    if "path drift" in line.lower():
        return "path_drift"
    elif "novelty starvation" in line.lower():
        return "novelty_starvation"
    elif "429" in line.lower():
        return "capacity_429"
    elif "key hydration" in line.lower():
        return "key_hydration"
    elif "duplicate" in line.lower():
        return "dup_poison"
    elif "publisher noop" in line.lower():
        return "publisher_noop_cap"
    elif "ghost build" in line.lower():
        return "ghost_build"
    elif "write service" in line.lower():
        return "write_service"
    elif "bootstrap service" in line.lower():
        return "bootstrap_service"
    elif "shim 5xx" in line.lower():
        return "shim_5xx"
    else:
        return "unknown"

def print_playbook():
    print("{:<20} {:<50} {:<50} {:<20}".format("Class", "Root Cause", "Fix", "Prior Refs"))
    print("-" * 140)
    for class_name, details in REMEDIES.items():
        print("{:<20} {:<50} {:<50} {:<20}".format(
            class_name,
            details["root"],
            details["fix"],
            ", ".join(map(str, details["prior_refs"]))
        ))

def main():
    if len(sys.argv) == 1:
        print_playbook()
    elif len(sys.argv) == 2:
        arg = sys.argv[1]
        if arg in REMEDIES:
            details = REMEDIES[arg]
            print(f"Class: {arg}")
            print(f"Root Cause: {details['root']}")
            print(f"Fix: {details['fix']}")
            print(f"Prior Refs: {', '.join(map(str, details['prior_refs']))}")
        else:
            try:
                with open(arg, 'r') as file:
                    lines = file.readlines()
                    tally = defaultdict(int)
                    for line in lines:
                        class_name = classify_line(line)
                        tally[class_name] += 1

                    for class_name, count in tally.items():
                        print(f"{class_name}: {count}")
                        details = remedy(class_name)
                        print(f"  Root Cause: {details['root']}")
                        print(f"  Fix: {details['fix']}")
                        print(f"  Prior Refs: {', '.join(map(str, details['prior_refs']))}")
            except FileNotFoundError:
                print(f"Error: File '{arg}' not found.")
    else:
        print("Usage: python failure_playbook.py [class_name|log_file_path]")

if __name__ == "__main__":
    main()