import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from chtec.formats import default_registry
from chtec.llm import LocalSettings
from chtec.ui.window import MainWindow


def test_dataset_menu_is_present_and_collection_starts_disabled():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(default_registry(), LocalSettings())
    assert hasattr(w, "dataset_controller")
    c = w.dataset_controller
    assert not c.enabled
    assert c.menu.title() == "Датасет"
    assert c.collect_action.isCheckable()
    w.dirty = False
    w.close()
    app.processEvents()


def test_dataset_settings_roundtrip_and_corrupt_fallback(tmp_path):
    from chtec.ui.dataset_controller import DatasetPreferences

    path = tmp_path / "prefs.json"
    p = DatasetPreferences(path)
    p.save(str(tmp_path / "collection"), True, "Book 1883")
    restored = DatasetPreferences(path)
    assert restored.enabled and restored.group == "Book 1883"
    assert restored.folder == str(tmp_path / "collection")
    path.write_text("broken")
    assert not DatasetPreferences(path).enabled


def wait_collection(app, controller):
    from PySide6.QtTest import QTest

    for _ in range(500):
        app.processEvents()
        if controller.worker is None and not controller.queue:
            return
        QTest.qWait(10)
    raise AssertionError("Dataset worker did not finish")


def test_pending_document_edit_cannot_export_previous_approval(tmp_path):
    from chtec.layout import TextBlock
    from tests.test_layout import page

    app = QApplication.instance() or QApplication([])
    w = MainWindow(default_registry(), LocalSettings())
    c = w.dataset_controller
    errors = []
    c.error = errors.append
    c.persist(str(tmp_path / "collection"), True, "book")
    p = page()
    p.replace_blocks([TextBlock("one", "Correct", (0.1, 0.1, 0.6, 0.3))])
    w.add_pages([p])
    c.flush()
    wait_collection(app, c)
    sample = c.service().list_samples()[0]
    c.action("approve", {"id": sample["id"], "fingerprint": sample["fingerprint"]})
    wait_collection(app, c)
    assert c.service().list_samples()[0]["status"] == "approved"
    p.edit_block("one", "Still being edited")
    # Export is requested before the debounce timer can reconcile the edit.
    c.action("export", {"target": str(tmp_path / "export"), "split": False})
    wait_collection(app, c)
    assert c.service().list_samples()[0]["status"] == "stale"
    assert not (tmp_path / "export").exists() and errors
    assert p.blocks[0].text == "Still being edited"
    w.dirty = False
    w.close()
    wait_collection(app, c)
    w.deleteLater()
    app.processEvents()


def test_close_flushes_last_pending_edit(tmp_path):
    from chtec.layout import TextBlock
    from tests.test_layout import page

    app = QApplication.instance() or QApplication([])
    w = MainWindow(default_registry(), LocalSettings())
    c = w.dataset_controller
    c.persist(str(tmp_path / "collection"), True, "book")
    p = page()
    p.replace_blocks([TextBlock("one", "Final edit", (0.1, 0.1, 0.6, 0.3))])
    w.add_pages([p])
    c.notify()
    w.dirty = False
    w.close()
    wait_collection(app, c)
    assert c.service().list_samples()[0]["text"] == "Final edit"
    w.deleteLater()
    app.processEvents()


def test_switching_pages_before_debounce_preserves_edited_page(tmp_path):
    import io

    from PIL import Image

    from chtec.layout import TextBlock
    from tests.test_layout import page

    app = QApplication.instance() or QApplication([])
    w = MainWindow(default_registry(), LocalSettings())
    c = w.dataset_controller
    c.persist(str(tmp_path / "collection"), True, "book")
    first, second = page(), page()
    data = io.BytesIO()
    Image.new("RGB", (800, 1000), "blue").save(data, "PNG")
    second.image_png = data.getvalue()
    first.replace_blocks([TextBlock("a", "Old", (0.1, 0.1, 0.6, 0.3))])
    second.replace_blocks([TextBlock("b", "Other page", (0.1, 0.1, 0.6, 0.3))])
    w.add_pages([first, second])
    w.page_list.setCurrentRow(0)
    c.flush()
    wait_collection(app, c)
    first.edit_block("a", "Latest edit")
    w.refresh_controls()
    w.page_list.setCurrentRow(1)
    c.flush()
    wait_collection(app, c)
    assert (
        next(s for s in c.service().list_samples() if s["block_id"] == "a")["text"] == "Latest edit"
    )
    w.dirty = False
    w.close()
    wait_collection(app, c)
    w.deleteLater()
    app.processEvents()
