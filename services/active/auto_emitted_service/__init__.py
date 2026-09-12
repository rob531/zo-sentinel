# services/ is a package so builder-emitted service dirs
# (services/staged/<name>/ -> services/active/<name>/) are importable via
# `python -m services.<stage>.<name>.contract` with relative intra-service
# imports that survive staged->active promotion without any rewrite.
