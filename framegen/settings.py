"""User settings shared by the menu, the launcher, and the saved file.

The list is split across settings_early.py, settings_mid.py, and settings_late.py.
"""
from .settings_early import *
from .settings_mid import SPECS_M
from .settings_late import SPECS_B

SPECS: tuple[Spec, ...] = SPECS_A + SPECS_M + SPECS_B
BY_KEY = {s.key: s for s in SPECS}


def _leave_dlss(values: dict, path: Path) -> dict:
    """DLSS is no longer in Keel. A saved NVIDIA DLSS choice becomes RTX Video Super Resolution."""
    if values.get("scaler") != "dlss":
        return values
    values = dict(values)
    values["scaler"] = "vsr"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, indent=2), encoding="utf-8")
    except OSError:
        pass
    return values


class Settings:
    """Current values, saved to JSON on every change."""

    def __init__(self, values: dict | None = None, path: Path | None = None) -> None:
        self.path = path
        self._listeners: list[Callable[[str], None]] = []
        self._v = {s.key: s.default for s in SPECS}
        self._saved = dict(self._v)
        for k, v in (values or {}).items():
            if k in BY_KEY and BY_KEY[k].saved and v in BY_KEY[k].choices:
                self._v[k] = self._saved[k] = v

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Settings":
        try:
            values = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            values = {}
        if not isinstance(values, dict):
            values = {}
        return cls(_leave_dlss(values, Path(path)), path)

    def __getattr__(self, key: str) -> Any:
        try:
            return self.__dict__["_v"][key]
        except KeyError:
            raise AttributeError(key) from None

    def values(self) -> dict:
        return dict(self._v)

    def watch(self, fn: Callable[[str], None]) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def override(self, key: str, value: Any) -> None:
        self._put(key, value, save=False)

    def set(self, key: str, value: Any) -> None:
        self._put(key, value, save=True)

    def _put(self, key: str, value: Any, save: bool) -> None:
        if value not in BY_KEY[key].choices:
            raise ValueError(f"{key}: {value!r} is not one of {BY_KEY[key].choices}")
        changed = self._v[key] != value
        self._v[key] = value
        if save and BY_KEY[key].saved:
            self._saved[key] = value
            self.save()
        if changed:
            for fn in list(self._listeners):
                fn(key)

    def step(self, key: str, delta: int, allowed=None) -> Any:
        choices = BY_KEY[key].choices
        i = choices.index(self._v[key])
        for _ in range(len(choices)):
            i = (i + delta) % len(choices)
            if allowed is None or allowed(key, choices[i]):
                self.set(key, choices[i])
                break
        return self._v[key]

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._saved, indent=2), encoding="utf-8")
        except OSError as e:
            print(f"[settings] couldn't save {self.path}: {e}")


def specs_json(specs: tuple[Spec, ...] = SPECS) -> dict:
    """The registry for the launcher's Settings page."""
    return {
        "path": str(DEFAULT_PATH),
        "impact_pc": IMPACT_PC,
        "specs": [{"key": s.key, "label": s.label, "group": s.group, "help": s.help, "default": s.default,
                   "live": s.live, "saved": s.saved, "later": s.later, "quick": s.quick, "impact": s.impact,
                   "experimental": s.experimental,
                   "sources": list(s.sources) or None,
                   "elsewhere": s.elsewhere or None,
                   "applies": {"key": s.applies[0], "values": list(s.applies[1])} if s.applies else None,
                   "choices": [{"value": v, "name": s.name(v), "hint": s.hints.get(v, "")} for v in s.choices]}
                  for s in specs],
    }


if __name__ == "__main__":
    Settings.load()
    print(json.dumps(specs_json()))
