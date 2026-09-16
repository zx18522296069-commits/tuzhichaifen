from __future__ import annotations

import json
import logging
import re
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from config import Settings
from drive_client import DriveClient, DriveError
from excel_writer import validate_result, write_result
from image_parser import (
    ImageParseError,
    _COMPACT_PART_RE,
    _NAMED_PART_RE,
    _PART_RE,
    _UNNAMED_PART_RE,
    _ocr_page,
    _parse_parts,
    _parse_program,
    _parse_weight,
    main_name_from_filename,
    parse_image,
)
from matcher import MatchError, match_with_priority
from models import ImageData, ImagePart, SourcePart
from pdf_renderer import PdfRenderError, extract_pdf_text_pages, render_pdf_page
from source_cache import load_channel_cached
from source_reader import SourceReadError


LOGGER = logging.getLogger("tuzhichaifen")
IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}
PDF_MIME_TYPE = "application/pdf"
SUPPORTED_INPUT_MIME_TYPES = IMAGE_MIME_TYPES | {PDF_MIME_TYPE}
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class RunItem:
    filename: str
    status: str
    output: str | None = None
    error: str | None = None
    stage: str | None = None
    suggestion: str | None = None


def _parse_pdf_native_text(filename: str, page_texts: list[str]) -> ImageData:
    """Compatibility helper for single-block PDF text regression tests.

    Production multi-page PDFs are handled by ``_parse_pdf_by_page`` below so
    one bad page cannot force every good page through OCR.
    """
    native_text = "\n".join(
        page_text for page_text in page_texts if page_text and page_text.strip()
    ).strip()
    if not native_text:
        raise ImageParseError("PDF 不含可选择的原生文本")

    try:
        parts = _parse_parts(native_text)
        marked_weight_kg = _parse_weight(native_text)
    except ImageParseError as exc:
        raise ImageParseError(f"PDF 原生文本无法完整解析：{exc}") from exc

    program_no = (
        _parse_program(native_text)
        if re.search(r"\bN\s*[0-9ILSB]{2,}\b", native_text.upper())
        else ""
    )
    return ImageData(
        original_filename=filename,
        main_name=main_name_from_filename(filename),
        program_no=program_no,
        marked_weight_kg=marked_weight_kg,
        parts=parts,
        ocr_text=native_text,
    )


def _parse_pdf_page_parts(text: str) -> tuple[ImagePart, ...]:
    """Parse one PDF page without requiring its first index to be 1.

    A later PDF page may legitimately contain indexes 4,5,6.  The page itself
    must still be contiguous and unambiguous; the final PDF-wide merge later
    requires the complete 1..N sequence.
    """
    found: dict[int, Counter[ImagePart]] = {}
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line.upper().replace("—", "-").replace("–", "-")).strip()
        match = _PART_RE.search(line) or _COMPACT_PART_RE.search(line)
        if match:
            part = ImagePart(
                index=int(match.group("index")),
                order_no=match.group("order").upper(),
                drawing_no=match.group("drawing").upper(),
                thickness=float(match.group("thickness").replace(",", ".")),
                base_quantity=int(match.group("base_quantity")),
                bevel=match.group("bevel").upper(),
                split_quantity=int(match.group("split_quantity")),
            )
        else:
            named_match = _NAMED_PART_RE.search(line)
            if named_match:
                part = ImagePart(
                    index=int(named_match.group("index")),
                    order_no="",
                    drawing_no=re.sub(r"\s+", "", named_match.group("drawing")).upper(),
                    thickness=float(named_match.group("thickness").replace(",", ".")),
                    base_quantity=int(named_match.group("base_quantity")),
                    bevel=named_match.group("bevel").upper(),
                    split_quantity=int(named_match.group("split_quantity")),
                )
            else:
                unnamed_match = _UNNAMED_PART_RE.search(line)
                if not unnamed_match:
                    continue
                part = ImagePart(
                    index=int(unnamed_match.group("index")),
                    order_no="",
                    drawing_no="",
                    thickness=float(unnamed_match.group("thickness").replace(",", ".")),
                    base_quantity=int(unnamed_match.group("base_quantity")),
                    bevel=unnamed_match.group("bevel").upper(),
                    split_quantity=int(unnamed_match.group("split_quantity")),
                )
        found.setdefault(part.index, Counter())[part] += 1

    if not found:
        raise ImageParseError("本页未识别出零件索引")

    selected: list[ImagePart] = []
    for index, candidates in sorted(found.items()):
        highest = max(candidates.values())
        best = [part for part, count in candidates.items() if count == highest]
        if len(best) != 1:
            raise ImageParseError(f"本页零件序号 {index} 存在冲突：{best}")
        selected.append(best[0])

    indexes = [part.index for part in selected]
    expected = list(range(indexes[0], indexes[-1] + 1))
    if indexes != expected:
        raise ImageParseError(f"本页零件序号不连续：{indexes}")
    return tuple(selected)


def _parse_pdf_page_ocr(path: Path) -> tuple[tuple[ImagePart, ...], str]:
    """OCR exactly one rendered PDF page and choose a stable part-list result."""
    part_texts, all_texts = _ocr_page(path)
    candidates: list[tuple[ImagePart, ...]] = []
    for text in part_texts:
        try:
            candidates.append(_parse_pdf_page_parts(text))
        except ImageParseError:
            continue
    if not candidates:
        raise ImageParseError("本页 OCR 未识别出连续零件索引")

    longest = max(len(candidate) for candidate in candidates)
    complete = [candidate for candidate in candidates if len(candidate) == longest]
    signatures = [tuple((part.index, part.drawing_no) for part in candidate) for candidate in complete]
    counts = Counter(signatures)
    highest = max(counts.values())
    best = [signature for signature, count in counts.items() if count == highest]
    if len(best) != 1:
        raise ImageParseError("本页零件图号在多次 OCR 中不一致")
    selected_signature = best[0]
    selected_candidates = [
        candidate
        for candidate in complete
        if tuple((part.index, part.drawing_no) for part in candidate) == selected_signature
    ]
    selected = Counter(selected_candidates).most_common(1)[0][0]
    return selected, "\n".join(all_texts)


def _has_part_list_hint(text: str) -> bool:
    normalized = text.upper()
    if "零件索引" in text:
        return True
    return bool(re.search(r"(?m)^\s*\d+\s+.*?\bT\s*\d+", normalized))


def _merge_pdf_page_parts(page_parts: list[tuple[ImagePart, ...]]) -> tuple[ImagePart, ...]:
    by_index: dict[int, set[ImagePart]] = {}
    for parts in page_parts:
        for part in parts:
            by_index.setdefault(part.index, set()).add(part)
    if not by_index:
        raise ImageParseError("PDF 各页均未识别出零件索引")

    merged: list[ImagePart] = []
    for index in sorted(by_index):
        options = by_index[index]
        if len(options) != 1:
            raise ImageParseError(f"跨页零件序号 {index} 内容不一致：{sorted(map(str, options))}")
        merged.append(next(iter(options)))

    indexes = [part.index for part in merged]
    expected = list(range(1, len(merged) + 1))
    if indexes != expected:
        raise ImageParseError(f"跨页零件序号不连续：{indexes}")
    return tuple(merged)


def _weight_if_present(text: str) -> float | None:
    try:
        return _parse_weight(text)
    except ImageParseError:
        return None


def _consistent_weight(values: list[float], source_name: str) -> float:
    if not values:
        raise ImageParseError("PDF 各页均未识别到钢板重量")
    first = values[0]
    if any(abs(value - first) > 0.01 for value in values[1:]):
        raise ImageParseError(f"PDF 多页{source_name}钢板重量不一致：{values}")
    return first


def _program_from_texts(texts: list[str]) -> str:
    programs: list[str] = []
    for text in texts:
        if not re.search(r"\bN\s*[0-9ILSB]{2,}\b", text.upper()):
            continue
        try:
            programs.append(_parse_program(text))
        except ImageParseError:
            continue
    if not programs:
        return ""
    counts = Counter(programs)
    highest = max(counts.values())
    return next(program for program in reversed(programs) if counts[program] == highest)


def _parse_pdf_by_page(
    filename: str,
    pdf_path: Path,
    page_texts: list[str],
    rendered_dir: Path,
) -> tuple[ImageData, list[int]]:
    """Parse a PDF page-by-page, OCRing only pages whose native list is unusable."""
    page_parts: list[tuple[ImagePart, ...]] = []
    native_texts: list[str] = []
    ocr_texts: list[str] = []
    native_weights: list[float] = []
    ocr_weights: list[float] = []
    ocr_pages: list[int] = []

    for page_index, native_text in enumerate(page_texts):
        native_text = native_text or ""
        if native_text.strip():
            native_texts.append(native_text)
            weight = _weight_if_present(native_text)
            if weight is not None:
                native_weights.append(weight)
        try:
            parts = _parse_pdf_page_parts(native_text)
            page_parts.append(parts)
            LOGGER.info("PDF 第 %d 页使用原生文本", page_index + 1)
            continue
        except ImageParseError as native_exc:
            LOGGER.info("PDF 第 %d 页原生零件清单不足，单页 OCR 回退：%s", page_index + 1, native_exc)

        rendered = render_pdf_page(pdf_path, rendered_dir, page_index)
        ocr_pages.append(page_index + 1)
        try:
            parts, ocr_text = _parse_pdf_page_ocr(rendered)
            page_parts.append(parts)
            ocr_texts.append(ocr_text)
            weight = _weight_if_present(ocr_text)
            if weight is not None:
                ocr_weights.append(weight)
            LOGGER.info("PDF 第 %d 页 OCR 解析成功", page_index + 1)
        except ImageParseError as ocr_exc:
            # A text-based drawing-only continuation page may legitimately have
            # no part list.  Empty/scanned pages are kept strict because there
            # is no reliable evidence that a required list was not lost.
            if native_text.strip() and not _has_part_list_hint(native_text):
                LOGGER.info("PDF 第 %d 页判定为无零件清单的图形页，跳过", page_index + 1)
                continue
            raise ImageParseError(
                f"PDF 第 {page_index + 1} 页原生文本与 OCR 均无法安全读取零件清单：{ocr_exc}"
            ) from ocr_exc

    parts = _merge_pdf_page_parts(page_parts)
    marked_weight_kg = (
        _consistent_weight(native_weights, "原生文本")
        if native_weights
        else _consistent_weight(ocr_weights, "OCR")
    )
    program_no = _program_from_texts(native_texts) or _program_from_texts(ocr_texts)
    all_text = "\n".join(native_texts + ocr_texts)
    return (
        ImageData(
            original_filename=filename,
            main_name=main_name_from_filename(filename),
            program_no=program_no,
            marked_weight_kg=marked_weight_kg,
            parts=parts,
            ocr_text=all_text,
        ),
        ocr_pages,
    )


def _failure_detail(filename: str, exc: Exception) -> tuple[str, str, str]:
    """把技术异常转成前端和日志中可直接处理的中文说明。"""
    raw = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, PdfRenderError):
        return (
            "PDF 图纸读取失败",
            f"文件={filename}；{raw}",
            "确认该 PDF 可正常打开；系统会逐页读取 PDF 原生文本，只有异常页才进入 OCR 回退。",
        )
    if isinstance(exc, ImageParseError) and Path(filename).suffix.lower() == ".pdf":
        return (
            "PDF 内容提取失败",
            f"文件={filename}；{raw}",
            "系统会逐页优先读取 PDF 原生文本，只对异常页使用 OCR，并在最后执行跨页序号和冲突校验。请检查报错页的零件清单和钢板重量。",
        )
    if isinstance(exc, ImageParseError):
        return (
            "图片识别失败",
            f"文件={filename}；OCR/版式识别未通过：{raw}",
            "重点检查序号后的零件图号是否清晰、完整，以及钢板重量是否可读；标题栏和程序号不作为失败条件。确认后保留原文件重新执行。",
        )
    if isinstance(exc, MatchError):
        return (
            "基础数据严格匹配失败",
            f"文件={filename}；识别结果无法在“正在加工”或“拆图模版”中唯一核对：{raw}",
            "核对订单号、图号、厚度、坡口和基础件数；补齐或修正对应订单汇总表后重新执行。系统不会猜测或强行匹配。",
        )
    if isinstance(exc, SourceReadError):
        return (
            "订单基础表读取失败",
            f"文件={filename}；基础表结构或字段异常：{raw}",
            "检查对应订单汇总表是否可打开，并确认订单号、图号、厚度、件数、坡口和重量列完整。",
        )
    if isinstance(exc, DriveError):
        return (
            "云盘读写失败",
            f"文件={filename}；Google Drive 操作失败：{raw}",
            "检查该文件和目标目录权限、网络及 Google 凭据后重新执行。",
        )
    if isinstance(exc, OSError):
        return (
            "本地文件处理失败",
            f"文件={filename}；下载、生成或保存时失败：{raw}",
            "检查源文件是否损坏、文件名是否合法，并重新执行该图纸文件。",
        )
    return (
        "结果文件校验失败",
        f"文件={filename}；Excel 生成或公式校验未通过：{raw}",
        "检查该图纸文件的提取数据及生成结果；问题未修复前不会上传 Excel，也不会给原文件加“完成_”。",
    )


def _load_channel(
    drive: DriveClient,
    folder_id: str,
    channel_name: str,
    workdir: Path,
) -> list[SourcePart]:
    return load_channel_cached(drive, folder_id, channel_name, workdir)


def run_drive(
    settings: Settings,
    output_dir: Path,
    dry_run: bool = False,
    only: str | None = None,
) -> list[RunItem]:
    drive = DriveClient()
    pending = drive.find_child_folder(settings.root_folder_id, settings.pending_folder_name)
    processing = drive.find_child_folder(settings.root_folder_id, settings.processing_folder_name)
    templates = drive.find_child_folder(settings.root_folder_id, settings.template_folder_name)
    results = drive.find_child_folder(settings.root_folder_id, settings.result_folder_name)

    candidates = [
        item
        for item in drive.list_children(pending["id"])
        if item.get("mimeType") in SUPPORTED_INPUT_MIME_TYPES
        and not item.get("name", "").startswith(settings.completed_prefix)
        and not Path(item.get("name", "")).stem.endswith("_完成")
        and (not only or only in item.get("name", ""))
    ]
    LOGGER.info("扫描到 %d 个未完成图纸文件（图片/PDF）", len(candidates))
    if not candidates:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    run_items: list[RunItem] = []
    with tempfile.TemporaryDirectory(prefix="tuzhichaifen-") as temp_name:
        temp_dir = Path(temp_name)
        primary_parts = _load_channel(drive, processing["id"], settings.processing_folder_name, temp_dir)
        fallback_parts = _load_channel(drive, templates["id"], settings.template_folder_name, temp_dir)
        LOGGER.info("基础数据：正在加工 %d 条，拆图模版 %d 条", len(primary_parts), len(fallback_parts))

        for item in candidates:
            filename = item["name"]
            try:
                suffix = Path(filename).suffix.lower() or ".jpg"
                local_input = temp_dir / "inputs" / f"{item['id']}{suffix}"
                drive.download(item["id"], local_input)
                if item.get("mimeType") == PDF_MIME_TYPE:
                    native_page_texts = extract_pdf_text_pages(local_input)
                    image, ocr_pages = _parse_pdf_by_page(
                        filename,
                        local_input,
                        native_page_texts,
                        temp_dir / "rendered" / item["id"],
                    )
                    if ocr_pages:
                        LOGGER.info("PDF 逐页解析成功：%s｜仅 OCR 页=%s", filename, ocr_pages)
                    else:
                        LOGGER.info("PDF 逐页原生文本解析成功：%s（未使用 OCR）", filename)
                else:
                    image = parse_image(local_input, original_filename=filename)
                matches = match_with_priority(image, primary_parts, fallback_parts)
                result_path = output_dir / f"{image.main_name}_完成.xlsx"
                write_result(result_path, image, matches)
                validate_result(result_path, expected_rows=len(matches))
                if not dry_run:
                    drive.upsert_file(results["id"], result_path, XLSX_MIME)
                    drive.rename(item["id"], f"{settings.completed_prefix}{filename}")
                run_items.append(RunItem(filename, "dry-run" if dry_run else "completed", result_path.name))
                LOGGER.info("%s：%s -> %s", "验证完成" if dry_run else "处理完成", filename, result_path.name)
            except (DriveError, ImageParseError, MatchError, SourceReadError, ValueError, OSError) as exc:
                stage, detail, suggestion = _failure_detail(filename, exc)
                error = f"{stage}｜{detail}｜处理建议：{suggestion}"
                run_items.append(RunItem(filename, "failed", error=error, stage=stage, suggestion=suggestion))
                LOGGER.error("处理失败｜阶段=%s｜%s｜处理建议=%s", stage, detail, suggestion)

    report_path = output_dir / "run_report.json"
    report_path.write_text(json.dumps([asdict(item) for item in run_items], ensure_ascii=False, indent=2), encoding="utf-8")
    return run_items