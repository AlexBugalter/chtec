import threading

import pytest

from chtec.domain import AppError, Document, Page
from chtec.services import DocumentService


def test_partial_ocr_results_are_reported_before_failure():
    class FailingModel:
        def recognize(self, page, instruction):
            if page.number == 2:
                raise AppError("Модель недоступна")
            return "Распознано"

    document = Document([Page(b"", "s", 1), Page(b"", "s", 2)])
    results = []
    with pytest.raises(AppError):
        DocumentService(FailingModel()).recognize(
            document.pages, "", threading.Event(), lambda i, text: results.append((i, text))
        )
    assert results == [(0, "Распознано")]
    # Worker reports data; only the UI thread may change the live document.
    assert document.pages[0].text == ""


def test_cancellation_stops_between_pages():
    stop = threading.Event()

    class Model:
        def recognize(self, page, instruction):
            stop.set()
            return "Первая"

    results = []
    completed = DocumentService(Model()).recognize(
        [Page(b"", "s", 1), Page(b"", "s", 2)],
        "",
        stop,
        lambda i, text: results.append((i, text)),
    )
    assert completed == 1
    assert results == [(0, "Первая")]
