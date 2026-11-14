"""逐号核对拆图索引与原始汇总表，按空格分隔的业务字段成段对照。

所有猜测仅用于异常展示，绝不能当作成功匹配或写回依据。
"""

from __future__ import annotations

from matcher import _same_drawing, find_candidates
from models import ImageData, ImagePart, SourcePart


FIELD_LABELS = (
    ("order_no", "订单号"),
    ("drawing_no", "图号"),
    ("thickness", "厚度"),
    ("bevel", "坡口"),
    ("base_quantity", "基础件数"),
)


def _value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value if value is not None else "")


def _same_field(key: str, part: ImagePart, source: SourcePart) -> bool:
    if key == "drawing_no":
        return bool(part.drawing_no) and _same_drawing(part, source)
    if key == "order_no":
        return bool(part.order_no) and part.order_no == source.order_no
    return getattr(part, key) == getattr(source, key)


def _source_ref(source: SourcePart) -> dict:
    return {
        "path": source.source_path,
        "workbook": source.source_workbook,
        "row": source.row_number,
        "order_no": source.order_no,
        "drawing_no": source.drawing_no,
        "thickness": _value(source.thickness),
        "bevel": source.bevel,
        "base_quantity": source.base_quantity,
    }


def _difference(part: ImagePart, source: SourcePart) -> list[dict]:
    differences = []
    for key, label in FIELD_LABELS:
        if _same_field(key, part, source):
            continue
        actual, expected = _value(getattr(part, key)), _value(getattr(source, key))
        differences.append({
            "field": key,
            "label": label,
            "actual": actual or "（未识别）",
            "expected": expected or "（空值）",
        })
    return differences


def _suspected_source(part: ImagePart, sources: list[SourcePart]) -> tuple[SourcePart | None, list[SourcePart]]:
    """Only expose one likely source when at least four of five fields agree.

    Two equally plausible sources are *not* a reliable diagnosis. This helper
    never returns an automatic match to the production calculation.
    """
    if not sources:
        return None, []
    scores = [(sum(_same_field(key, part, source) for key, _ in FIELD_LABELS), source) for source in sources]
    highest = max(score for score, _ in scores)
    choices = [source for score, source in scores if score == highest]
    if highest >= 4 and len(choices) == 1:
        return choices[0], []
    if highest >= 4:
        return None, choices[:5]
    # An exact order+part identity is still useful when thickness/quantity and
    # bevel all differ; never call this a definitive character-level mismatch.
    matching_identity = [
        source for source in sources
        if part.order_no and part.drawing_no
        and source.order_no == part.order_no and _same_drawing(part, source)
    ]
    if len(matching_identity) == 1:
        return matching_identity[0], []
    if len(matching_identity) > 1:
        return None, matching_identity[:5]
    return None, []


def inspect_split(image: ImageData, primary: list[SourcePart], fallback: list[SourcePart]) -> dict:
    """Review every index, even when previous indexes have failed.

    Return only JSON-compatible public diagnostic fields (never raw workbook
    bytes or credentials). The caller blocks upload if error_count > 0.
    """
    rows: list[dict] = []
    for part in image.parts:
        actual = {key: _value(getattr(part, key)) for key, _ in FIELD_LABELS}
        base = {
            "index": part.index,
            "drawing_no": part.drawing_no,
            "actual": actual,
            "status": "待核对",
            "source": None,
            "differences": [],
            "alternatives": [],
            "searched_workbooks": [],
            "note": "",
        }
        # 只凭厚度、坡口和基础件数，即便在所有汇总表里恰好只
        # 找到一条，也不算核对成功。图号未读到就不能认定其身份。
        if not part.drawing_no:
            physical_primary = [
                source for source in primary
                if (not part.order_no or source.order_no == part.order_no)
                and source.thickness == part.thickness
                and source.base_quantity == part.base_quantity
                and source.bevel == part.bevel
            ]
            physical_fallback = [
                source for source in fallback
                if (not part.order_no or source.order_no == part.order_no)
                and source.thickness == part.thickness
                and source.base_quantity == part.base_quantity
                and source.bevel == part.bevel
            ]
            candidates = physical_primary or physical_fallback
            base["alternatives"] = [_source_ref(source) for source in candidates[:5]]
            base["candidate_count"] = len(candidates)
            base["note"] = (
                f"图号未能从原图识别，不能与汇总表唯一核对；"
                f"仅按厚度/坡口/件数找到 {len(candidates)} 条候选（仅列前 5 条），"
                "需补读 PDF 原文或 OCR，禁止猜测图号"
            )
            rows.append(base)
            continue

        primary_exact = find_candidates(part, primary)
        fallback_exact = find_candidates(part, fallback) if not primary_exact else []
        exact = primary_exact or fallback_exact
        if len(exact) == 1:
            source = exact[0]
            base["status"] = "通过"
            base["source"] = _source_ref(source)
            base["note"] = "图纸未标注坡口，已按 W 归类" if part.bevel_unmarked else "五项核对通过"
            if part.drawing_no and part.drawing_no != source.drawing_no:
                base["note"] += "；图号采用历史已确认的等价写法"
            rows.append(base)
            continue
        if len(exact) > 1:
            base["note"] = "相同条件找到多条汇总表记录，不能唯一匹配"
            base["alternatives"] = [_source_ref(source) for source in exact[:5]]
            rows.append(base)
            continue

        # Prefer the current orders; look in the templates only when no close
        # primary candidate can be established. This follows normal precedence.
        candidate, alternatives = _suspected_source(part, primary)
        if candidate is None and not alternatives:
            candidate, alternatives = _suspected_source(part, fallback)
        if candidate is not None:
            base["source"] = _source_ref(candidate)
            base["differences"] = _difference(part, candidate)
            base["status"] = "不一致" if base["differences"] else "待核对"
            base["note"] = "仅为唯一疑似对应行；差异未自动修正，必须复核原图和汇总表"
        elif alternatives:
            base["note"] = "存在多个同样接近的汇总表记录，无法确定哪一行正确"
            base["alternatives"] = [_source_ref(source) for source in alternatives]
        else:
            # Even when no row is close enough, tell the operator which
            # authoritative order workbooks were actually searched.
            same_order = [
                source for source in primary + fallback
                if part.order_no and source.order_no == part.order_no
            ]
            seen = set()
            for source in same_order:
                key = (source.source_path, source.source_workbook)
                if key not in seen:
                    base["searched_workbooks"].append({"path": key[0], "workbook": key[1]})
                    seen.add(key)
            base["note"] = (
                "已找到该订单的汇总表，但没有能唯一对应的零件行；请核对图纸和汇总表"
                if base["searched_workbooks"]
                else "未找到能够确认归属的汇总表记录；无法推断是哪一段错误"
            )
        rows.append(base)

    errors = sum(row["status"] != "通过" for row in rows)
    return {
        "filename": image.original_filename,
        "board_id": image.main_name,
        "checked_count": len(rows),
        "ok_count": len(rows) - errors,
        "error_count": errors,
        "rows": rows,
    }
