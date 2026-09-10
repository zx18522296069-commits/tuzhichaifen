from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from config import Settings
from drive_client import DriveClient, DriveError
from excel_writer import validate_result, write_result
from image_parser import ImageParseError, parse_image
from matcher import MatchError, match_with_priority
from models import SourcePart
from source_reader import SourceReadError, read_summary_workbook


LOGGER = logging.getLogger("tuzhichaifen")
IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_MIME = "application/vnd.ms-excel.sheet.macroenabled.12"
WORKBOOK_MIME_TYPES = {XLSX_MIME, XLSM_MIME}


@dataclass
class RunItem:
    filename: str
    status: str
    output: str | None = None
    error: str | None = None


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
        if item.get("mimeType") in IMAGE_MIME_TYPES
        and not item.get("name", "").startswith(settings.completed_prefix)
        and not Path(item.get("name", "")).stem.endswith("_完成")
        and (not only or only in item.get("name", ""))
    ]
    LOGGER.info("扫描到 %d 张未完成图片", len(candidates))
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
                local_image = temp_dir / "images" / f"{item['id']}{suffix}"
                drive.download(item["id"], local_image)
                image = parse_image(local_image, original_filename=filename)
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
                run_items.append(RunItem(filename, "failed", error=str(exc)))
                LOGGER.error("处理失败：%s：%s", filename, exc)

    report_path = output_dir / "run_report.json"
    report_path.write_text(json.dumps([asdict(item) for item in run_items], ensure_ascii=False, indent=2), encoding="utf-8")
    return run_items
