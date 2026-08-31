# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.


try:
    from app.main import app
except ImportError:

    def __getattr__(name):
        if name == "app":
            from app.main import app
        
            globals()["app"] = app
            return app
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["app"]

# Re-export dependency_overrides so files can do `from app import dependency_overrides`
# and then `app.dependency_overrides[get_session] = ...`.
# Lazy-loaded because app/__init__.py is imported before app.main in some CI paths.
_loaded_overrides = None


def __getattr__(name):
    global _loaded_overrides
    if name == "dependency_overrides":
        if _loaded_overrides is None:
            _loaded_overrides = app.dependency_overrides
        return _loaded_overrides
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")