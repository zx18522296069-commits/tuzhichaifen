from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook

from models import SourcePart


class SourceReadError(RuntimeError):
    pass


def _normal(value: object) -> str:
    return (
        str(value or "")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .strip()
        .upper()
        .replace("—", "-")
        .replace("–", "-")
    )


def _order_number(value: object) -> str:
    candidates = re.findall(r"(?:[A-Z0-9]+(?:-[A-Z0-9]+){2,}|\d{8,}-\d{4})", _normal(value))
    if not candidates:
        raise SourceReadError(f"无法从订单字段读取订单号：{value!r}")
    return candidates[-1]


def read_summary_workbook(path: Path, source_path: str) -> list[SourcePart]:
    keep_vba = path.suffix.lower() == ".xlsm"
    formula_wb = load_workbook(path, data_only=False, read_only=True, keep_vba=keep_vba)
    value_wb = load_workbook(path, data_only=True, read_only=True, keep_vba=keep_vba)

    header_row = None
    headers: dict[str, int] = {}
    value_ws = None
    for worksheet in value_wb.worksheets:
        max_header_row = min(worksheet.max_row or 0, 20)
        if max_header_row < 1:
            continue
        for row in worksheet.iter_rows(min_row=1, max_row=max_header_row):
            values = [_normal(cell.value) for cell in row]
            if "订单号" in values and "图号" in values and "厚度" in values and "件数" in values:
                value_ws = worksheet
                header_row = row[0].row
                headers = {value: index + 1 for index, value in enumerate(values) if value}
                break
        if value_ws is not None:
            break
    if value_ws is None or not header_row:
        raise SourceReadError(f"{path.name} 未找到汇总表表头")
    formula_ws = formula_wb[value_ws.title]

    def column(*names: str) -> int:
        for name in names:
            key = _normal(name)
            if key in headers:
                return headers[key]
        raise SourceReadError(f"{path.name} 缺少字段：{' / '.join(names)}")

    order_col = column("订单号")
    drawing_col = column("图号")
    thickness_col = column("厚度")
    quantity_col = column("件数")
    weight_col = None
    weight_multiplier = None
    for name, multiplier in (
        ("总净重(kg)", 1.0),
        ("总重量(kg)", 1.0),
        ("总净重(t)", 1000.0),
        ("总重量(t)", 1000.0),
        ("重量", 1000.0),
    ):
        key = _normal(name)
        if key in headers:
            weight_col = headers[key]
            weight_multiplier = multiplier
            break
    if weight_col is None or weight_multiplier is None:
        raise SourceReadError(f"{path.name} 缺少总重量字段（kg、t 或模板重量）")

    bevel_col = headers.get("坡口")
    if bevel_col is None:
        # Some production summary sheets keep the bevel values in the normal
        # detail column but leave that column's heading blank.  Only accept a
        # single column between quantity and weight whose populated detail
        # values are unambiguous bevel codes; otherwise stop instead of
        # guessing.
        candidates: list[int] = []
        for candidate in range(quantity_col + 1, weight_col):
            values: list[str] = []
            for row_no in range(header_row + 1, value_ws.max_row + 1):
                if not _normal(value_ws.cell(row_no, drawing_col).value):
                    continue
                value = _normal(value_ws.cell(row_no, candidate).value)
                if value:
                    values.append(value)
            if values and all(re.fullmatch(r"[A-Z][A-Z0-9]{0,3}", value) for value in values):
                candidates.append(candidate)
        if len(candidates) != 1:
            raise SourceReadError(f"{path.name} 缺少字段：坡口")
        bevel_col = candidates[0]
    result: list[SourcePart] = []
    for row_no in range(header_row + 1, value_ws.max_row + 1):
        drawing = _normal(value_ws.cell(row_no, drawing_col).value)
        if not drawing:
            continue
        try:
            order = _order_number(value_ws.cell(row_no, order_col).value)
            thickness = float(value_ws.cell(row_no, thickness_col).value)
            quantity = int(value_ws.cell(row_no, quantity_col).value)
        except (TypeError, ValueError, SourceReadError):
            continue
        bevel = _normal(value_ws.cell(row_no, bevel_col).value)
        cached_weight = value_ws.cell(row_no, weight_col).value
        if isinstance(cached_weight, (int, float)):
            total_weight_kg = float(cached_weight) * weight_multiplier
        else:
            formula = formula_ws.cell(row_no, weight_col).value
            raise SourceReadError(f"{path.name} 第 {row_no} 行总重量没有可读取的已确认值：{formula!r}")
        result.append(
            SourcePart(
                order_no=order,
                drawing_no=drawing,
                thickness=thickness,
                base_quantity=quantity,
                bevel=bevel,
                total_weight_kg=total_weight_kg,
                source_path=source_path,
                source_workbook=path.name,
            )
        )
    return result
