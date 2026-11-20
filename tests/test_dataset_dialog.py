import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from chtec.ui.dataset_dialog import DatasetManagerDialog


@pytest.fixture
def dialog(tmp_path):
    app = QApplication.instance() or QApplication([])
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFF)
    path = tmp_path / "sample.png"
    assert image.save(str(path))
    sample = dict(
        id="one",
        fingerprint="v1",
        status="draft",
        text="  Точный\nтекст  ",
        image_path=str(path),
        group="Книга",
        uncertain=False,
    )
    widget = DatasetManagerDialog([sample])
    widget.show()
    app.processEvents()
    yield widget, sample
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_preview_and_explicit_approval(dialog):
    widget, sample = dialog
    events = []
    widget.action_requested.connect(lambda action, payload: events.append((action, payload)))
    assert widget.text.toPlainText() == sample["text"]
    assert not widget.image.pixmap().isNull()
    widget.approve_button.click()
    assert events == [("approve", {"id": "one", "fingerprint": "v1"})]


def test_select_all_requests_bulk_approval(dialog):
    widget, sample = dialog
    other = dict(sample, id='two', fingerprint='v2')
    widget.set_samples([sample, other])
    events = []
    widget.action_requested.connect(lambda action, payload: events.append((action, payload)))
    widget.select_all_button.click()
    assert len(widget.samples_list.selectedItems()) == 2
    widget.approve_selected_button.click()
    assert events == [('approve_many', [
        {'id': 'one', 'fingerprint': 'v1'}, {'id': 'two', 'fingerprint': 'v2'},
    ])]


def test_dirty_text_requires_save_then_fresh_review(dialog):
    widget, sample = dialog
    events = []
    widget.action_requested.connect(lambda action, payload: events.append((action, payload)))
    widget.text.setPlainText("Исправлено\n ")
    assert not widget.approve_button.isEnabled()
    widget.save_button.click()
    assert events == [
        (
            "save",
            dict(id="one", fingerprint="v1", text="Исправлено\n ", group="Книга", uncertain=False),
        )
    ]
    assert not widget.approve_button.isEnabled()
    widget.set_samples([dict(sample, fingerprint="v2", text="Исправлено\n ")])
    widget.approve_button.click()
    assert events[-1] == ("approve", {"id": "one", "fingerprint": "v2"})


def test_refresh_selection_filters_and_busy(dialog):
    widget, sample = dialog
    widget.set_samples([dict(sample, id="two", status="excluded"), sample])
    widget.select_sample("two")
    widget.set_samples([sample, dict(sample, id="two", status="excluded")])
    assert widget.current_sample["id"] == "two"
    widget.status_filter.setCurrentIndex(widget.status_filter.findData("draft"))
    assert widget.samples_list.count() == 1
    assert widget.current_sample["id"] == "one"
    widget.set_busy(True)
    assert not widget.approve_button.isEnabled()
    assert not widget.refresh_button.isEnabled()
    widget.set_busy(False)
    assert widget.approve_button.isEnabled()


def test_background_refresh_preserves_uncommitted_example_edit(dialog):
    widget, sample = dialog
    widget.text.setPlainText("Моя несохранённая правка")
    widget.set_busy(True)
    widget.set_samples([sample])
    widget.set_busy(False)
    assert widget.text.toPlainText() == "Моя несохранённая правка"
    assert widget.save_button.isEnabled()


def test_invalid_image_uncertainty_and_empty_text_block_approval(dialog):
    widget, sample = dialog
    widget.set_samples([dict(sample, image_path="missing.png")])
    assert not widget.approve_button.isEnabled()
    widget.set_samples([dict(sample, uncertain=True)])
    assert not widget.approve_button.isEnabled()
    widget.set_samples([dict(sample, text=" \n ")])
    assert not widget.approve_button.isEnabled()


def test_export_only_requests_local_new_target(dialog, tmp_path, monkeypatch):
    widget, _ = dialog
    events = []
    widget.action_requested.connect(lambda action, payload: events.append((action, payload)))
    target = str(tmp_path / "new-export")
    monkeypatch.setattr(
        "chtec.ui.dataset_dialog.QFileDialog.getSaveFileName", lambda *args, **kwargs: (target, "")
    )
    widget.export_button.click()
    assert events == [("export", {"target": target, "split": False})]


def test_export_cancel_existing_path_and_empty_collection(dialog, tmp_path, monkeypatch):
    widget, _ = dialog
    events = []
    widget.action_requested.connect(lambda action, payload: events.append((action, payload)))
    monkeypatch.setattr(
        "chtec.ui.dataset_dialog.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(tmp_path), ""),
    )
    widget.export_button.click()
    assert not events
    assert widget.error_label.text()
    monkeypatch.setattr(
        "chtec.ui.dataset_dialog.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: ("", ""),
    )
    widget.export_button.click()
    assert not events
    widget.set_samples([])
    assert widget.current_sample is None
    assert not widget.approve_button.isEnabled()
    assert not widget.save_button.isEnabled()
    assert not widget.exclude_button.isEnabled()


def test_exclusion_and_unsaved_group_cannot_approve(dialog):
    widget, _ = dialog
    events = []
    widget.action_requested.connect(lambda action, payload: events.append((action, payload)))
    widget.group.setText("Другое дело")
    assert not widget.approve_button.isEnabled()
    widget.exclude_button.click()
    assert events == [("exclude", {"id": "one"})]


def test_revision_preserves_logical_selection_with_new_id(dialog):
    widget, sample = dialog
    first = dict(sample, page_hash="page-a", block_id="block-a")
    second = dict(sample, id="second", page_hash="page-b", block_id="block-b")
    widget.set_samples([first, second])
    widget.select_sample("second")
    widget.text.setPlainText("Правка второго примера")
    widget.save_button.click()
    widget.set_samples(
        [
            first,
            dict(second, id="new-second", fingerprint="v2", text="Правка второго примера"),
        ]
    )
    assert widget.current_sample["id"] == "new-second"
    assert widget.text.toPlainText() == "Правка второго примера"


@pytest.mark.parametrize("invalid_flag", ["deleted", "source_invalid"])
def test_invalid_source_cannot_be_approved(dialog, invalid_flag):
    widget, sample = dialog
    events = []
    widget.action_requested.connect(lambda action, payload: events.append((action, payload)))
    widget.set_samples([dict(sample, **{invalid_flag: True})])
    assert not widget.approve_button.isEnabled()
    widget.approve_button.click()
    assert not events
