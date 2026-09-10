from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImagePart:
    index: int
    order_no: str
    drawing_no: str
    thickness: float
    base_quantity: int
    bevel: str
    split_quantity: int


@dataclass(frozen=True)
class ImageData:
    original_filename: str
    main_name: str
    program_no: str
    marked_weight_kg: float
    parts: tuple[ImagePart, ...]
    ocr_text: str


@dataclass(frozen=True)
class SourcePart:
    order_no: str
    drawing_no: str
    thickness: float
    base_quantity: int
    bevel: str
    total_weight_kg: float
    source_path: str
    source_workbook: str


@dataclass(frozen=True)
class MatchedPart:
    image: ImagePart
    source: SourcePart

    @property
    def total_weight_t(self) -> float:
        return self.source.total_weight_kg / 1000.0

    @property
    def unit_weight_t(self) -> float:
        return self.total_weight_t / self.image.base_quantity

    @property
    def split_weight_kg(self) -> float:
        return self.unit_weight_t * self.image.split_quantity * 1000.0

