from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import fitz
from openpyxl import load_workbook

from config import Settings
from drive_client import DriveClient
from excel_writer import validate_result, write_result
from image_parser import ImageParseError, _ocr_page, _parse_parts_from_variants, parse_image
from matcher import match_with_priority
from pdf_renderer import extract_pdf_text_pages
from pipeline import PDF_MIME_TYPE, _load_channel, _parse_pdf_by_page


SAMPLES = [
    "完成_废183.2 T70退0.pdf",
    "完成_废209.4 T80退0.pdf",
    "完成_#2327 T80退0.pdf",
    "完成_#2185 T80退0.pdf",
    "完成_#2334 T80退700X1650.pdf",
    "完成_#2326 T50退2230X4150.pdf",
    # 已确认是真实 2 页生产 PDF，用于多页解析/合并回归。
    "完成_#2330 T30退0.pdf",
    "完成_#2323 T150退400X1760.jpg",
    "完成_#2333 T60退0.jpg",
]


def _facts(path: Path, expected_rows: int) -> list[list[object]]:
    workbook = load_workbook(path, data_only=True)
    worksheet = workbook["拆图明细"]
    rows: list[list[object]] = []
    for row in range(5, 5 + expected_rows):
        rows.append([worksheet.cell(row, column).value for column in range(2, 10)])
    workbook.close()
    return rows


def _same_value(left: object, right: object) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 0.02
    return left == right


def _compare(generated: Path, existing: Path, expected_rows: int) -> list[str]:
    current = _facts(generated, expected_rows)
    historical = _facts(existing, expected_rows)
    problems: list[str] = []
    if len(current) != len(historical):
        return [f"行数不一致：当前={len(current)} 历史={len(historical)}"]
    columns = ["订单号", "图号", "厚度", "基础件数", "拆分数量", "基础总重量T", "单件重量T", "拆分重量KG"]
    for row_index, (new_row, old_row) in enumerate(zip(current, historical), start=1):
        for column_index, (new_value, old_value) in enumerate(zip(new_row, old_row)):
            if not _same_value(new_value, old_value):
                problems.append(
                    f"第{row_index}项 {columns[column_index]} 不一致：当前={new_value!r} 历史={old_value!r}"
                )
    return problems


def _image_debug(path: Path) -> dict[str, object]:
    """只输出数量和数字候选，避免把整张生产图 OCR 文本写进公开 CI 日志。"""
    try:
        part_texts, texts = _ocr_page(path)
        recognized_parts = None
        try:
            recognized_parts = len(_parse_parts_from_variants(part_texts))
        except ImageParseError:
            pass
        tokens: list[str] = []
        for text in texts:
            for token in re.findall(r"(?<!\d)\d{2,7}(?:[.,%]\d{1,3})?(?!\d)", text):
                normalized = token.replace(",", ".")
                if normalized not in tokens:
                    tokens.append(normalized)
                if len(tokens) >= 20:
                    break
            if len(tokens) >= 20:
                break
        return {
            "part_ocr_variants": len(part_texts),
            "recognized_parts": recognized_parts,
            "numeric_candidates": tokens,
        }
    except Exception as exc:
        return {"debug_error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    settings = Settings.from_env()
    drive = DriveClient()
    pending = drive.find_child_folder(settings.root_folder_id, settings.pending_folder_name)
    processing = drive.find_child_folder(settings.root_folder_id, settings.processing_folder_name)
    templates = drive.find_child_folder(settings.root_folder_id, settings.template_folder_name)
    results = drive.find_child_folder(settings.root_folder_id, settings.result_folder_name)

    pending_items = {item["name"]: item for item in drive.list_children(pending["id"])}
    result_items: dict[str, list[dict]] = {}
    for item in drive.list_children(results["id"]):
        result_items.setdefault(item["name"], []).append(item)

    missing = [name for name in SAMPLES if name not in pending_items]
    if missing:
        raise RuntimeError(f"历史源文件缺失：{missing}")

    report: list[dict] = []
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="historical-regression-") as temp_name:
        root = Path(temp_name)
        primary_parts = _load_channel(drive, processing["id"], settings.processing_folder_name, root / "sources")
        fallback_parts = _load_channel(drive, templates["id"], settings.template_folder_name, root / "sources")

        for source_name in SAMPLES:
            item = pending_items[source_name]
            original_name = source_name[len(settings.completed_prefix):] if source_name.startswith(settings.completed_prefix) else source_name
            suffix = Path(source_name).suffix.lower()
            local_input = root / "inputs" / source_name
            case: dict[str, object] = {
                "source": source_name,
                "type": suffix.lstrip("."),
                "pages": None,
                "route": "image-ocr" if suffix != ".pdf" else None,
                "ocr_pages": [],
                "processing": "PENDING",
            }
            try:
                drive.download(item["id"], local_input)
                page_count = 1
                ocr_pages: list[int] = []

                if item.get("mimeType") == PDF_MIME_TYPE or suffix == ".pdf":
                    with fitz.open(local_input) as document:
                        page_count = document.page_count
                    page_texts = extract_pdf_text_pages(local_input)
                    image, ocr_pages = _parse_pdf_by_page(
                        original_name,
                        local_input,
                        page_texts,
                        root / "rendered" / item["id"],
                    )
                    route = "pdf-native" if not ocr_pages else "pdf-pagewise-ocr"
                else:
                    image = parse_image(local_input, original_filename=original_name)
                    route = "image-ocr"

                case["pages"] = page_count
                case["route"] = route
                case["ocr_pages"] = ocr_pages
                case["parts"] = len(image.parts)
                case["program"] = image.program_no
                case["marked_weight_kg"] = image.marked_weight_kg

                matches = match_with_priority(image, primary_parts, fallback_parts)
                generated = root / "generated" / f"{image.main_name}_完成.xlsx"
                write_result(generated, image, matches)
                validate_result(generated, expected_rows=len(matches))
                case["processing"] = "PASS"

                result_name = generated.name
                case["output"] = result_name
                existing_matches = result_items.get(result_name, [])
                if not existing_matches:
                    # 缺少旧成品只能说明没有逐字段基准，不能把已经成功完成解析/匹配/生成的样本判失败。
                    case["historical_compare"] = "NO_BASELINE"
                elif len(existing_matches) != 1:
                    raise RuntimeError(f"历史结果 {result_name} 应唯一，实际 {len(existing_matches)} 个")
                else:
                    existing = root / "existing" / result_name
                    drive.download(existing_matches[0]["id"], existing)
                    differences = _compare(generated, existing, len(matches))
                    case["differences"] = differences
                    case["historical_compare"] = "PASS" if not differences else "FAIL"
                    if differences:
                        failures.append(f"{source_name}: " + "；".join(differences[:8]))
            except Exception as exc:
                case["processing"] = "ERROR"
                case["historical_compare"] = "ERROR"
                case["error"] = f"{type(exc).__name__}: {exc}"
                if suffix in {".jpg", ".jpeg", ".png"} and local_input.exists():
                    case["image_debug"] = _image_debug(local_input)
                failures.append(f"{source_name}: {type(exc).__name__}: {exc}")

            report.append(case)
            print("HISTORICAL_CASE=" + json.dumps(case, ensure_ascii=False), flush=True)

    processing_pass = sum(1 for item in report if item.get("processing") == "PASS")
    baseline_pass = sum(1 for item in report if item.get("historical_compare") == "PASS")
    no_baseline = sum(1 for item in report if item.get("historical_compare") == "NO_BASELINE")
    fail_count = sum(1 for item in report if item.get("processing") == "ERROR" or item.get("historical_compare") == "FAIL")
    print("HISTORICAL_REGRESSION_REPORT=" + json.dumps(report, ensure_ascii=False), flush=True)
    print(
        f"HISTORICAL_REGRESSION_SUMMARY=total:{len(report)},processing_pass:{processing_pass},"
        f"baseline_pass:{baseline_pass},no_baseline:{no_baseline},fail:{fail_count}",
        flush=True,
    )
    if failures:
        print("HISTORICAL_REGRESSION_FAILURES=" + json.dumps(failures, ensure_ascii=False), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
