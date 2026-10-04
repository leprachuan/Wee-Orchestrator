"""Shared, ordered Wee model favorites persisted outside catalog caches."""
import json
import os
import tempfile
import threading
from pathlib import Path

FAVORITES_PATH = Path(__file__).resolve().parent / "config" / "model_favorites.json"


class ModelFavorites:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else FAVORITES_PATH
        self._lock = threading.RLock()

    @staticmethod
    def validate(models):
        if not isinstance(models, list) or len(models) > 100:
            raise ValueError("models must be an array of at most 100 model IDs")
        result = []
        for model in models:
            if not isinstance(model, str):
                raise ValueError("Each favorite must be a model ID string")
            model = model.strip()
            provider, separator, name = model.partition("/")
            if provider not in {"ollama", "openrouter", "lmstudio"} or not separator or not name or len(model) > 256 or any(c.isspace() for c in model):
                raise ValueError("Use a provider-qualified model ID, such as ollama/qwen3:8b or openrouter/openai/gpt-4.1-mini")
            if model not in result:
                result.append(model)
        return result

    def load(self):
        with self._lock:
            if not self.path.exists():
                return {"version": 1, "models": []}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Favorites JSON must be an object")
            return {"version": 1, "models": self.validate(data.get("models"))}

    def save(self, models):
        data = {"version": 1, "models": self.validate(models)}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                    temporary = handle.name
                    json.dump(data, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        return data

    def prioritize(self, models):
        favorites = self.load()["models"]
        rank = {model: index for index, model in enumerate(favorites)}
        result = []
        seen = set()
        for original in models:
            if original["id"] in seen:
                continue
            seen.add(original["id"])
            entry = dict(original)
            entry["favorite"] = entry["id"] in rank
            if entry["favorite"]:
                entry["provider_group"] = entry.get("group")
                entry["group"] = "Favorites"
            result.append(entry)
        return sorted(result, key=lambda entry: rank.get(entry["id"], len(rank)))


_store = ModelFavorites()


def get_model_favorites():
    return _store
