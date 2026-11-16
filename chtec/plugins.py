from importlib.metadata import entry_points

from chtec.formats.base import FormatRegistry


def load_plugins(registry: FormatRegistry) -> list[str]:
    """Load explicitly installed trusted packages; return actionable failures."""
    errors = []
    for entry in entry_points(group="chtec.formats"):
        try:
            entry.load()(registry)
        except Exception as exc:
            errors.append(f"Расширение {entry.name}: {exc}")
    return errors
