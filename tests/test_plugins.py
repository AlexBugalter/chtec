import csv

import pytest

from chtec.domain import Document, Page
from chtec.formats import default_registry
from examples.csv_plugin.chtec_csv import CsvExporter, register


def test_format_plugin_exports_unicode_and_blocks_spreadsheet_formulas(tmp_path):
    registry = default_registry()
    register(registry)
    path = tmp_path / "result.csv"
    registry.export_file(Document([Page(b"", "=malicious", 1, "Текст рукописи")]), path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[1] == ["1", "'=malicious", "Текст рукописи"]


def test_plugin_cannot_replace_existing_handler():
    registry = default_registry()
    register(registry)
    handler = registry.exporters[".csv"]
    with pytest.raises(ValueError, match="зарегистрирован"):
        registry.register_exporter(CsvExporter())
    assert registry.exporters[".csv"] is handler
