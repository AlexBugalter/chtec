import csv
from pathlib import Path

from chtec.domain import Document
from chtec.formats.base import Exporter, FormatRegistry


class CsvExporter(Exporter):
    extensions = (".csv",)
    label = "CSV (страница, источник, текст)"

    def write(self, document: Document, path: Path) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["page", "source", "text"])
            for index, page in enumerate(document.pages, 1):
                # Spreadsheet applications may interpret untrusted text as formulas.
                safe = [
                    "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
                    for value in (page.source, page.text)
                ]
                writer.writerow([index, *safe])


def register(registry: FormatRegistry) -> None:
    registry.register_exporter(CsvExporter())
