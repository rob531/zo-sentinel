import json
import os
from pathlib import Path
from typing import Any, TypeVar
from dataclasses import dataclass

T = TypeVar('T')

@dataclass
class SettingDef:
    name: str
    key: str
    value: Any
    type: str
    description: str
    label: str
    category: str


class SettingsService:
    _instance = None

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.environ.get('APP_SETTINGS_PATH', 'config/app_settings.json')
        self.config_path = config_path
        self._config = None
        self._load_config()

    def _load_config(self):
        path = Path(self.config_path)
        if path.exists():
            with open(path, 'r') as f:
                self._config = json.load(f)
        else:
            self._config = {"categories": {}}

    def get(self, key: str, default: Any = None) -> Any:
        for category_data in self._config.get("categories", {}).values():
            if key in category_data:
                return category_data[key].get("value", default)
        return default

    def get_typed(self, key: str, typ: type[T], default: T) -> T:
        value = self.get(key, default)
        if isinstance(value, typ):
            return value
        try:
            return typ(value)
        except (ValueError, TypeError):
            return default

    def list_settings(self) -> list[SettingDef]:
        settings = []
        for category_name, category_data in self._config.get("categories", {}).items():
            for key, cfg in category_data.items():
                settings.append(SettingDef(
                    name=cfg.get("label", key),
                    key=key,
                    value=cfg.get("value"),
                    type=cfg.get("type", "str"),
                    description=cfg.get("description", ""),
                    label=cfg.get("label", key),
                    category=category_name
                ))
        return settings

    def list_categories(self) -> list[str]:
        return sorted(self._config.get("categories", {}).keys())


def get_setting(key: str, default: Any = None) -> Any:
    return SettingsService().get(key, default)


def get_int_setting(key: str, default: int = 0) -> int:
    return SettingsService().get_typed(key, int, default)


def get_bool_setting(key: str, default: bool = False) -> bool:
    return SettingsService().get_typed(key, bool, default)


def get_float_setting(key: str, default: float = 0.0) -> float:
    return SettingsService().get_typed(key, float, default)


if __name__ == "__main__":
    import tempfile
    import shutil

    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "app_settings.json")

    config_data = {
        "categories": {
            "general": {
                "site_name": {"value": "MyApp", "type": "str", "description": "Site name", "label": "Site Name"},
                "scan_interval": {"value": 30, "type": "int", "description": "Scan interval", "label": "Scan Interval"}
            },
            "thresholds": {
                "score_threshold": {"value": 0.75, "type": "float", "description": "Score threshold", "label": "Score Threshold"}
            },
            "branding": {
                "logo_url": {"value": "https://example.com/logo.png", "type": "str", "description": "Logo URL", "label": "Logo URL"},
                "primary_color": {"value": "#FF5733", "type": "str", "description": "Primary color", "label": "Primary Color"}
            }
        }
    }

    with open(config_path, 'w') as f:
        json.dump(config_data, f)

    service = SettingsService(config_path)

    result = get_int_setting("scan_interval", 0)
    assert isinstance(result, int), f"Expected int, got {type(result)}"

    missing = service.get("nonexistent_key", "default_value")
    assert missing == "default_value", f"Expected 'default_value', got {missing}"

    settings = service.list_settings()
    assert len(settings) == 5, f"Expected 5 settings, got {len(settings)}"

    categories = service.list_categories()
    assert len(categories) == 3, f"Expected 3 categories, got {len(categories)}"

    shutil.rmtree(temp_dir)
    print("PASS")