"""Only application-authored UI strings are translated, never document content."""

import json
import re
from importlib.resources import files

LANGUAGE = "ru"
CATALOG = json.loads(files("chtec.ui").joinpath("en.json").read_text(encoding="utf-8"))
RENDERED = {}
PATTERNS = []
for source in CATALOG:
    if "{0}" in source:
        parts = re.split(r"(\{\d+\})", source)
        pattern = "".join("(.*?)" if re.fullmatch(r"\{\d+\}", p) else re.escape(p) for p in parts)
        PATTERNS.append((re.compile("^" + pattern + "$", re.DOTALL), source))


def tr(source, *args):
    template = CATALOG.get(source, source) if LANGUAGE == "en" else source
    value = template.format(*args) if args else template
    if len(RENDERED) > 4000:
        RENDERED.clear()
    RENDERED[value] = (source, args)
    return value


def translate_message(message):
    record = RENDERED.get(message)
    if record:
        return tr(record[0], *record[1])
    if message in CATALOG:
        return tr(message)
    for pattern, source in PATTERNS:
        match = pattern.fullmatch(message)
        if match:
            return tr(source, *match.groups())
    return message


def model_entry_label(entry):
    labels = {
        True: "{0} · изображения",
        False: "{0} · только текст",
        None: "{0} · возможности не указаны",
    }
    return tr(labels[entry.vision], entry.title)
