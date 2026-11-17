from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from chtec.domain_errors import AppError
from chtec.layout import TextBlock, validate_blocks

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_PAGES = 200
MAX_PIXELS = 40_000_000
MAX_IMAGE_SIDE = 2400
MAX_DOCUMENT_IMAGE_BYTES = 60 * 1024 * 1024


@dataclass
class Revision:
    text: str
    reason: str = "Изменение"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    blocks: list[TextBlock] = field(default_factory=list)
    unplaced_text: str = ""


@dataclass
class Page:
    image_png: bytes
    source: str
    number: int
    text: str = ""
    history: list[Revision] = field(default_factory=list)
    source_path: str = ""
    blocks: list[TextBlock] = field(default_factory=list)
    unplaced_text: str = ""

    def snapshot(self, reason):
        self._future = []
        self.history.append(
            Revision(self.text, reason, blocks=list(self.blocks), unplaced_text=self.unplaced_text)
        )
        self.history = self.history[-50:]

    def replace_blocks(self, blocks, reason="Разметка страницы", unplaced_text=None):
        blocks = validate_blocks(blocks)
        unplaced_text = self.unplaced_text if unplaced_text is None else unplaced_text
        if not isinstance(unplaced_text, str):
            raise AppError("Некорректный непривязанный текст.")
        if blocks != self.blocks or unplaced_text != self.unplaced_text:
            self.snapshot(reason)
            self.blocks = blocks
            self.unplaced_text = unplaced_text
            self.text = "\n\n".join(
                [b.text for b in blocks] + ([unplaced_text] if unplaced_text else [])
            )

    def edit_block(self, block_id, text, angle=None, reason="Правка блока"):
        if block_id not in {b.id for b in self.blocks}:
            raise AppError("Текстовый блок отсутствует.")
        self.replace_blocks(
            [
                replace(b, text=text, angle=b.angle if angle is None else angle)
                if b.id == block_id
                else b
                for b in self.blocks
            ],
            reason,
        )

    def update_block_geometry(self, block_id, rect, angle=None):
        if block_id not in {b.id for b in self.blocks}:
            raise AppError("Текстовый блок отсутствует.")
        blocks = [
            replace(
                b,
                rect=tuple(rect),
                angle=b.angle if angle is None else angle,
                quad=b.quad if tuple(rect) == b.rect else None,
                verified=True,
            )
            if b.id == block_id
            else b
            for b in self.blocks
        ]
        self.replace_blocks(blocks, "Изменение границ зоны")

    def update_block_quad(self, block_id, points):
        if block_id not in {b.id for b in self.blocks}:
            raise AppError("Текстовый блок отсутствует.")
        blocks = [
            replace(b.with_quad(points), verified=True) if b.id == block_id else b
            for b in self.blocks
        ]
        self.replace_blocks(blocks, "Изменение формы зоны")

    def delete_blocks(self, ids, keep_text=False):
        selected = set(ids)
        if not selected or not selected <= {b.id for b in self.blocks}:
            raise AppError("Выберите существующие зоны.")
        unplaced = self.unplaced_text
        if keep_text:
            unplaced = "\n\n".join(
                t for t in [unplaced, *[b.text for b in self.blocks if b.id in selected]] if t
            )
        self.replace_blocks(
            [b for b in self.blocks if b.id not in selected],
            "Снятие привязки" if keep_text else "Удаление зон",
            unplaced_text=unplaced,
        )

    def replace_text(self, text: str, reason: str = "Изменение") -> None:
        if text != self.text:
            if self.blocks:
                raise AppError("Редактируйте выбранный блок, чтобы сохранить разметку.")
            self.snapshot(reason)
            self.text = text
            self.unplaced_text = ""

    def undo(self) -> bool:
        if not self.history:
            return False
        if not hasattr(self, "_future"):
            self._future = []
        self._future.append(
            Revision(self.text, blocks=list(self.blocks), unplaced_text=self.unplaced_text)
        )
        revision = self.history.pop()
        self.text = revision.text
        self.blocks = list(revision.blocks)
        self.unplaced_text = revision.unplaced_text
        return True

    def redo(self) -> bool:
        if not getattr(self, "_future", []):
            return False
        revision = self._future.pop()
        self.history.append(
            Revision(self.text, blocks=list(self.blocks), unplaced_text=self.unplaced_text)
        )
        self.history = self.history[-50:]
        self.text, self.blocks, self.unplaced_text = (
            revision.text,
            list(revision.blocks),
            revision.unplaced_text,
        )
        return True


@dataclass
class Document:
    pages: list[Page] = field(default_factory=list)
    title: str = "Рукопись"

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages)

    def append(self, pages: list[Page]) -> None:
        if len(self.pages) + len(pages) > MAX_PAGES:
            raise AppError(f"В документе может быть не более {MAX_PAGES} страниц.")
        validate_image_budget([*self.pages, *pages])
        self.pages.extend(pages)


def validate_image_budget(pages: list[Page]) -> None:
    if sum(len(page.image_png) for page in pages) > MAX_DOCUMENT_IMAGE_BYTES:
        raise AppError("Изображения проекта превышают 60 MiB. Разделите документ на части.")
