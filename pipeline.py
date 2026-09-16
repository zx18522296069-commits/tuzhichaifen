from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from config import Settings
from drive_client import DriveClient, DriveError
from excel_writer import validate_result, write_result
from image_parser import (
    ImageParseError,
    _parse_parts,
    _parse_program,
    _parse_weight,
    main_name_from_filename,
    parse_image,
    parse_images,
)
from matcher import MatchError, match_with_priority
from models import ImageData, SourcePart
from pdf_renderer import PdfRenderError, extract_pdf_text_pages, render_pdf_pages
from source_reader import SourceReadError, read_summary_workbook


LOGGER = logging.getLogger("tuzhichaifen")
IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}
PDF_MIME_TYPE = "application/pdf"
SUPPORTED_INPUT_MIME_TYPES = IMAGE_MIME_TYPES | {PDF_MIME_TYPE}
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_MIME = "application/vnd.ms-excel.sheet.macroenabled.12"
WORKBOOK_MIME_TYPES = {XLSX_MIME, XLSM_MIME}


@dataclass
class RunItem:
    filename: str
    status: str
    output: str | None = None
    error: str | None = None
    stage: str | None = None
    suggestion: str | None = None


def _parse_pdf_native_text(filename: str, page_texts: list[str]) -> ImageData:
    """Parse FastNEST/FastCAM PDF text without involving OCR.

    Production PDFs normally contain selectable text.  For those files the
    embedded text is the authoritative source because it is not affected by
    rendering DPI, anti-aliasing, or OCR ambiguity.  OCR is reserved for
    scanned/image-only PDFs and is invoked by ``run_drive`` only when this
    function cannot produce a complete safe result.
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


def _failure_detail(filename: str, exc: Exception) -> tuple[str, str, str]:
    """把技术异常转成前端和日志中可直接处理的中文说明。"""
    raw = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, PdfRenderError):
        return (
            "PDF 图纸读取失败",
            f"文件={filename}；{raw}",
            "确认该 PDF 可正常打开；系统会先直接读取 PDF 原生文本，只有扫描件才会进入 OCR 回退。",
        )
    if isinstance(exc, ImageParseError) and Path(filename).suffix.lower() == ".pdf":
        return (
            "PDF 内容提取失败",
            f"文件={filename}；{raw}",
            "系统已优先直接读取 PDF 原生文本；仅在原生文本不足时才使用 OCR 回退。请确认 PDF 由 FastNEST/FastCAM 正常导出，或扫描页中的零件清单和钢板重量完整可读。",
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
    source_parts: list[SourcePart] = []
    for folder_item in drive.list_children(folder_id):
        effective_id = drive.effective_folder_id(folder_item)
        if not effective_id:
            continue
        workbooks = [
            item
            for item in drive.list_children(effective_id)
            if item.get("mimeType") in WORKBOOK_MIME_TYPES
            and ("汇总表" in item.get("name", "") or "模板" in item.get("name", ""))
        ]
        for item in workbooks:
            local_path = workdir / channel_name / effective_id / item["name"]
            drive.download(item["id"], local_path)
            source_path = f"王振海/{channel_name}/{folder_item['name']}"
            try:
                source_parts.extend(read_summary_workbook(local_path, source_path))
            except SourceReadError as exc:
                # Source folders can contain legacy or auxiliary workbooks that
                # happen to use a summary-like filename.  Keep scanning other
                # orders; an image that depends on this file will still fail
                # strict matching and will not be renamed.
                LOGGER.warning("跳过不可读基础表：%s/%s：%s", source_path, item["name"], exc)
    return source_parts


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
                    try:
                        image = _parse_pdf_native_text(filename, native_page_texts)
                        LOGGER.info("PDF 原生文本解析成功：%s（未使用 OCR）", filename)
                    except ImageParseError as native_exc:
                        LOGGER.warning("PDF 原生文本不足，回退 OCR：%s：%s", filename, native_exc)
                        rendered_pages = render_pdf_pages(
                            local_input, temp_dir / "rendered" / item["id"]
                        )
                        try:
                            image = parse_images(rendered_pages, original_filename=filename)
                        except ImageParseError as ocr_exc:
                            raise ImageParseError(
                                f"PDF 原生文本提取未通过：{native_exc}；OCR 回退也未通过：{ocr_exc}"
                            ) from ocr_exc
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