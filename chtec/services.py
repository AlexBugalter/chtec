from threading import Event
from typing import Callable

from chtec.domain import Page
from chtec.llm import LanguageModel


class DocumentService:
    def __init__(self, model: LanguageModel):
        self.model = model

    def recognize(
        self,
        pages: list[Page],
        instruction: str,
        cancel: Event,
        on_page: Callable[[int, str], None],
    ) -> int:
        completed = 0
        for index, page in enumerate(pages):
            if cancel.is_set():
                break
            result = self.model.recognize(page, instruction)
            on_page(index, result)
            completed += 1
        return completed
