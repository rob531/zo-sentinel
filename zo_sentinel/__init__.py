"""zo_sentinel package marker.

This file MUST exist. Without it, ``zo_sentinel`` is a PEP 420 namespace
package, and Python's import machinery keeps scanning ``sys.path`` past the
local checkout. If any *other* ``zo_sentinel`` exists earlier-resolved on the
path (e.g. an older site-packages copy that predates the ingestor/promoters/
publisher subpackages added 2026-05-27), it shadows this one: ``goose_runner``
still imports ``zo_sentinel.build_routing`` (a flat module present in the old
copy) but ``python3 -m zo_sentinel.ingestor`` / ``zo_sentinel.promoters`` fail
with ``No module named`` because those subpackages only live here.

Making this a regular package pins resolution to the local checkout.
"""
