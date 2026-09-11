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
    # 同一订单的旧模板有时把订单号重复写进图号，而排版图只显示图号后缀。
    # 这里只接受带连字符边界的完整后缀；后续候选数量仍必须严格等于 1。
    if image.order_no and source.order_no == image.order_no:
        if source.drawing_no.endswith(f"-{image.drawing_no}"):
            return True
        if image.drawing_no.endswith(f"-{source.drawing_no}"):
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
    source_list = list(source_parts)
    candidates = find_candidates(image, source_list)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and image.order_no:
        same_order_physical = [
            source
            for source in source_list
            if source.order_no == image.order_no
            and _same_number(source.thickness, image.thickness)
            and source.base_quantity == image.base_quantity
            and source.bevel == image.bevel
        ]
        if len(same_order_physical) == 1:
            return same_order_physical[0]
    key = (
        f"订单={image.order_no or '图片未识别'}, 图号={image.drawing_no or '图片未识别'}, 厚度={image.thickness:g}, "
        f"坡口={image.bevel}, 基础件数={image.base_quantity}"
    )
    if not candidates:
        nearby = [
            source
            for source in source_list
            if (not image.order_no or source.order_no == image.order_no)
            and _same_drawing(image, source)
        ]
        details = "; ".join(
            f"T{item.thickness:g}/{item.base_quantity}J/{item.bevel} @ "
            f"{item.source_path}/{item.source_workbook}"
            for item in nearby[:5]
        )
        physical_details = "; ".join(
            f"{item.drawing_no} @ {item.source_path}/{item.source_workbook}"
            for item in same_order_physical[:5]
        ) if image.order_no else ""
        suffix = f"；同订单图号候选：{details}" if details else ""
        if physical_details:
            suffix += f"；同订单物理字段候选：{physical_details}"
        raise MatchError(f"无唯一基础数据：{key}（找到 0 条{suffix}）")
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
