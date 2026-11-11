"""Placeholder router co-emitted with this service's manifest so its
declared import_path (services.active.axis_probs_spread_api.router) resolves to a real, importable module in the
SAME change (FU-447). A manifest that names a module the change does not
ship is an orphan the promoter cannot read and a guaranteed
ModuleNotFoundError at mount. The build lane's router.py pass replaces
this stand-in with the data-layer-backed surface."""
from fastapi import APIRouter

router = APIRouter()
