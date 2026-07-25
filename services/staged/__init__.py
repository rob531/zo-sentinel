# services/staged/ -- the builder drops self-contained service dirs here
# (autonomous, single-file-per-concern via subrecipes/engine). A service in
# staged/ is INVENTORY: tested in isolation, reachable by nobody, costing
# nothing. staged/ is NOT scanned by generate_spine.py -- only active/ is.
# Promotion staged/ -> active/ is the reachability decision
# (tools/promote_staged_to_active.py), gated on the service's own contract.py.
