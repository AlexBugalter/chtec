from chtec.formats.base import FormatRegistry
from chtec.formats.exporters import (
    DocxExporter,
    HtmlExporter,
    PdfExporter,
    ProjectExporter,
    TextExporter,
)
from chtec.formats.importers import ImageImporter, PdfImporter, ProjectImporter


def default_registry() -> FormatRegistry:
    registry = FormatRegistry()
    for importer in (ImageImporter(), PdfImporter(), ProjectImporter()):
        registry.register_importer(importer)
    for exporter in (
        TextExporter(),
        HtmlExporter(),
        ProjectExporter(),
        DocxExporter(),
        PdfExporter(),
    ):
        registry.register_exporter(exporter)
    return registry
