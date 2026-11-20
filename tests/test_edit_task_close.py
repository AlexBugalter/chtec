from threading import Event
from types import SimpleNamespace

import pytest

from chtec.ui import window


@pytest.mark.parametrize("closing,cancelled", [(True, False), (False, True)])
def test_late_edit_result_does_not_open_dialog(monkeypatch, closing, cancelled):
    callbacks = []
    state = SimpleNamespace(
        current_page=SimpleNamespace(blocks=[], text="Исходный текст"),
        worker=None,
        close_after_task=closing,
        edit_request=SimpleNamespace(text=lambda: "Правка"),
        statusBar=lambda: SimpleNamespace(showMessage=lambda message: None),
        language_model=lambda: object(),
        run_task=lambda operation, callback: callbacks.append(callback),
    )

    def unexpected_dialog(*args):
        pytest.fail("A late result opened the proposal dialog")

    monkeypatch.setattr(window, "ProposalDialog", unexpected_dialog)
    window.MainWindow.edit_with_model(state)
    state.worker = SimpleNamespace(cancel=Event())
    if cancelled:
        state.worker.cancel.set()
    callbacks[0]("Исправленный текст")
