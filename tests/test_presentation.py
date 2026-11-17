import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit


def test_preferences_validate_and_corrupt_store_falls_back(tmp_path):
    from chtec.domain import AppError
    from chtec.ui.preferences import UiPreferences, UiPreferencesStore

    store = UiPreferencesStore(tmp_path / "ui.json")
    assert store.load() == UiPreferences()
    expected = UiPreferences(theme="dark", language="en", scale=150)
    store.save(expected)
    assert store.load() == expected
    for values in ({"scale": 201}, {"scale": True}, {"language": "xx"}, {"theme": "bad"}):
        with pytest.raises(AppError):
            UiPreferences(**values).validated()
    store.path.write_text("{bad", encoding="utf-8")
    assert store.load() == UiPreferences() and store.warning


def test_live_presentation_updates_bound_labels_not_user_text():
    from chtec.ui.preferences import UiPreferences
    from chtec.ui.presentation import PresentationManager

    app = QApplication.instance() or QApplication([])
    manager = PresentationManager(app, UiPreferences())
    label = QLabel("Настройки")
    text = QLineEdit("Настройки")
    manager.bind(label, "text", "Настройки")
    manager.apply(language="en", theme="dark", scale=150)
    assert label.text() == "Settings"
    assert text.text() == "Настройки"
    assert manager.preferences.scale == 150
    manager.apply(language="ru")
    assert label.text() == "Настройки"
    label.deleteLater()
    text.deleteLater()
    manager.close()


def test_error_and_progress_translation_preserves_dynamic_path():
    from chtec.ui import translations

    translations.LANGUAGE = "en"
    try:
        assert translations.translate_message("Поиск строк") == "Finding text lines"
        assert (
            translations.translate_message("Не удалось подготовить фрагмент: E:/Настройки.jpg")
            == "Could not prepare the area: E:/Настройки.jpg"
        )
    finally:
        translations.LANGUAGE = "ru"


def test_automatic_binding_does_not_restore_obsolete_dynamic_text():
    from chtec.ui.preferences import UiPreferences
    from chtec.ui.presentation import PresentationManager
    from chtec.ui.translations import tr

    app = QApplication.instance() or QApplication([])
    manager = PresentationManager(app, UiPreferences())
    label = QLabel(tr("Выберите платформу и модель."))
    manager.refresh()
    label.setText("LM Studio · Готово · model-X")
    manager.apply(language="en")
    assert label.text() == "LM Studio · Ready · model-X"
    label.deleteLater()
    manager.close()


def test_bound_labels_survive_render_cache_eviction():
    from chtec.ui import translations
    from chtec.ui.preferences import UiPreferences
    from chtec.ui.presentation import PresentationManager

    app = QApplication.instance() or QApplication([])
    manager = PresentationManager(app, UiPreferences())
    label = QLabel("Настройки")
    manager.bind(label, "text", "Настройки")
    manager.apply(language="en")
    translations.RENDERED.clear()
    manager.apply(language="ru")
    assert label.text() == "Настройки"
    label.deleteLater()
    manager.close()


def test_model_catalog_retranslates_without_selection_signals():
    from PySide6.QtWidgets import QComboBox

    from chtec.backends.catalog import ModelEntry
    from chtec.ui.preferences import UiPreferences
    from chtec.ui.presentation import PresentationManager

    app = QApplication.instance() or QApplication([])
    manager = PresentationManager(app, UiPreferences())
    combo = QComboBox()
    combo.addItem("Model · изображения", ModelEntry("model", "Model", vision=True))
    changes = []
    combo.currentIndexChanged.connect(changes.append)
    manager.apply(language="en")
    assert combo.currentText() == "Model · images"
    assert changes == [] and combo.currentData().id == "model"
    combo.deleteLater()
    manager.close()
