from __future__ import annotations

from collections.abc import Iterable

from models import ImageData, ImagePart, MatchedPart, SourcePart


class MatchError(RuntimeError):
    pass


SPECIAL_ORDER = "26511255302-0814"


def _same_number(left: float, right: float) -> bool:
    return abs(left - right) < 1e-9


def _same_drawing(image: ImagePart, source: SourcePart) -> bool:
    if not image.drawing_no:
        return True
    if source.drawing_no == image.drawing_no:
        return True
    if image.order_no != SPECIAL_ORDER or source.order_no != SPECIAL_ORDER:
        return False
    image_tokens = image.drawing_no.split("-")
    source_tokens = source.drawing_no.split("-")
    return len(image_tokens) >= 2 and len(source_tokens) >= 2 and image_tokens[-2:] == source_tokens[-2:]


def find_candidates(image: ImagePart, source_parts: Iterable[SourcePart]) -> list[SourcePart]:
    return [
        source
        for source in source_parts
        if (not image.order_no or source.order_no == image.order_no)
        and _same_drawing(image, source)
        and _same_number(source.thickness, image.thickness)
        and source.base_quantity == image.base_quantity
        and source.bevel == image.bevel
    ]


def match_one(image: ImagePart, source_parts: Iterable[SourcePart]) -> SourcePart:
    candidates = find_candidates(image, source_parts)
    if len(candidates) == 1:
        return candidates[0]
    key = (
        f"订单={image.order_no or '图片未识别'}, 图号={image.drawing_no or '图片未识别'}, 厚度={image.thickness:g}, "
        f"坡口={image.bevel}, 基础件数={image.base_quantity}"
    )
    if not candidates:
        raise MatchError(f"无唯一基础数据：{key}（找到 0 条）")
    paths = sorted({f"{item.source_path}/{item.source_workbook}" for item in candidates})
    raise MatchError(f"无唯一基础数据：{key}（找到 {len(candidates)} 条：{paths}）")


def match_image(image: ImageData, source_parts: Iterable[SourcePart]) -> tuple[MatchedPart, ...]:
    source_list = list(source_parts)
    return tuple(MatchedPart(part, match_one(part, source_list)) for part in image.parts)


def match_with_priority(
    image: ImageData,
    primary: Iterable[SourcePart],
    fallback: Iterable[SourcePart],
) -> tuple[MatchedPart, ...]:
    primary_list = list(primary)
    fallback_list = list(fallback)
    matches: list[MatchedPart] = []
    for part in image.parts:
        primary_candidates = find_candidates(part, primary_list)
        if len(primary_candidates) == 1:
            matches.append(MatchedPart(part, primary_candidates[0]))
            continue
        if len(primary_candidates) > 1:
            match_one(part, primary_list)
        matches.append(MatchedPart(part, match_one(part, fallback_list)))
    return tuple(matches)
