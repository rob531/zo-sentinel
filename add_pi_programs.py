#!/usr/bin/env python3
"""
Idempotent script to add Phase 8 PI daemon entry-points to supervisord_sentinel_full.conf.
Appends 6 program blocks if not already present; exits 0 silently if all 6 exist.
"""
import re
import sys

TARGET = '/home/workspace/zo_sentinel/supervisord_sentinel_full.conf'

PI_PROGRAMS = [
    {
        'name': 'pi_corpus_ingest',
        'file': 'pi_corpus_ingest.py',
        'stderr': 'pi_corpus_ingest.err.log',
        'stdout': 'pi_corpus_ingest.out.log',
    },
    {
        'name': 'pi_quarantine_reviewer',
        'file': 'pi_quarantine_reviewer.py',
        'stderr': 'pi_quarantine_reviewer.err.log',
        'stdout': 'pi_quarantine_reviewer.out.log',
    },
    {
        'name': 'pi_quarantine_promoter',
        'file': 'pi_quarantine_promoter.py',
        'stderr': 'pi_quarantine_promoter.err.log',
        'stdout': 'pi_quarantine_promoter.out.log',
    },
    {
        'name': 'pi_flagged_review_api',
        'file': 'pi_flagged_review_api.py',
        'stderr': 'pi_flagged_review_api.err.log',
        'stdout': 'pi_flagged_review_api.out.log',
    },
    {
        'name': 'pi_harness_runner',
        'file': 'pi_harness_runner.py',
        'stderr': 'pi_harness_runner.err.log',
        'stdout': 'pi_harness_runner.out.log',
    },
    {
        'name': 'pi_scorer',
        'file': 'pi_scorer.py',
        'stderr': 'pi_scorer.err.log',
        'stdout': 'pi_scorer.out.log',
    },
]


def program_block(p: dict) -> str:
    return f"""[program:{p['name']}]
command=python3 /home/workspace/zo_sentinel/{p['file']}
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
startretries=5
redirect_stderr=true
stdout_logfile=/home/workspace/logs/{p['stdout']}
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3
user=workspace
"""


def main():
    with open(TARGET, 'r') as f:
        content = f.read()

    # Count existing pi_* entries
    existing = re.findall(r'^\[program:(pi_[^\]]+)\]', content, re.MULTILINE)
    existing_set = set(existing)
    needed = [p for p in PI_PROGRAMS if p['name'] not in existing_set]

    if not needed:
        print('All 6 PI programs already present; nothing to do.')
        sys.exit(0)

    print(f'Adding {len(needed)} PI program(s) to {TARGET}:')
    for p in needed:
        print(f'  + {p["name"]}')

    # Append new blocks
    new_blocks = ''.join(program_block(p) for p in needed)
    if not content.endswith('\n'):
        content += '\n'
    content += '\n' + new_blocks

    with open(TARGET, 'w') as f:
        f.write(content)

    print('Done.')
    sys.exit(0)


if __name__ == '__main__':
    main()
