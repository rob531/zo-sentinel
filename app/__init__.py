# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

__all__ = ["app", "dependency_overrides"]


def __getattr__(name):
    # Fully lazy. app/__init__.py is imported before app.main in some CI paths,
    # so importing app.main eagerly here is unsafe. Keeping it lazy also means
    # `import app` no longer executes app.main (and therefore app.routers) --
    # see "Import-boundary incident (2026-08-16)" in CLAUDE.md.
    if name in ("app", "dependency_overrides"):
        from app.main import app as _app

        globals()["app"] = _app
        if name == "app":
            return _app
        overrides = _app.dependency_overrides
        globals()["dependency_overrides"] = overrides
        return overrides
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
