from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from models import SourcePart
from source_reader import SourceReadError, read_summary_workbook


LOGGER = logging.getLogger("tuzhichaifen")
CACHE_VERSION = 1
WORKBOOK_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
}


def _cache_file(cache_root: Path, channel_name: str) -> Path:
    safe_name = "processing" if channel_name == "正在加工" else "templates" if channel_name in {"拆图模版", "总拆图模版"} else channel_name
    return cache_root / f"{safe_name}.json"


def _read_cache(path: Path) -> dict[str, dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if payload.get("version") != CACHE_VERSION or not isinstance(payload.get("entries"), dict):
        return {}
    return payload["entries"]


def _write_cache(path: Path, entries: dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"version": CACHE_VERSION, "entries": entries}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        # 缓存仅用于提速；写缓存失败不能改变正式拆图业务结果。
        LOGGER.warning("基础数据缓存写入失败，继续按本次已读取数据执行：%s", exc)


def _signature(item: dict, effective_folder_id: str, folder_item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "modifiedTime": item.get("modifiedTime", ""),
        "size": str(item.get("size", "")),
        "folder_id": effective_folder_id,
        "folder_name": folder_item.get("name", ""),
    }


def _restore_parts(raw_parts: object) -> list[SourcePart]:
    if not isinstance(raw_parts, list):
        raise ValueError("缓存零件数据格式错误")
    return [SourcePart(**part) for part in raw_parts if isinstance(part, dict)]


def load_channel_cached(
    drive,
    folder_id: str,
    channel_name: str,
    workdir: Path,
    cache_root: Path = Path(".source_cache"),
) -> list[SourcePart]:
    """读取一个基础数据通道，只下载新增或发生变化的汇总表。

    Drive 目录和文件仍会在每次运行时重新枚举，以保证新增、修改、删除都能立即生效。
    缓存只替代“未变化 Excel 的正文下载 + openpyxl 重新解析”，不改变严格匹配规则。
    """
    cache_path = _cache_file(cache_root, channel_name)
    old_entries = _read_cache(cache_path)
    new_entries: dict[str, dict] = {}
    source_parts: list[SourcePart] = []
    reused = 0
    downloaded = 0
    cached_errors = 0

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
            file_id = item["id"]
            signature = _signature(item, effective_id, folder_item)
            cached = old_entries.get(file_id)
            if cached and cached.get("signature") == signature:
                try:
                    if cached.get("status") == "error":
                        cached_errors += 1
                        new_entries[file_id] = cached
                        LOGGER.warning(
                            "跳过未变化的不可读基础表（缓存）：王振海/%s/%s/%s：%s",
                            channel_name,
                            folder_item.get("name", ""),
                            item.get("name", ""),
                            cached.get("error", "读取失败"),
                        )
                        continue
                    restored = _restore_parts(cached.get("parts"))
                    source_parts.extend(restored)
                    new_entries[file_id] = cached
                    reused += 1
                    continue
                except (TypeError, ValueError):
                    # 缓存内容异常时直接退回正式下载/解析路径，不影响业务正确性。
                    pass

            local_path = workdir / channel_name / effective_id / item["name"]
            drive.download(file_id, local_path)
            downloaded += 1
            source_path = f"王振海/{channel_name}/{folder_item['name']}"
            try:
                parsed = read_summary_workbook(local_path, source_path)
                source_parts.extend(parsed)
                new_entries[file_id] = {
                    "signature": signature,
                    "status": "ok",
                    "parts": [asdict(part) for part in parsed],
                }
            except SourceReadError as exc:
                # 与旧逻辑一致：单个历史/辅助表不可读时跳过，严格匹配仍会阻止错误拆图。
                LOGGER.warning("跳过不可读基础表：%s/%s：%s", source_path, item["name"], exc)
                new_entries[file_id] = {
                    "signature": signature,
                    "status": "error",
                    "error": str(exc),
                    "parts": [],
                }

    _write_cache(cache_path, new_entries)
    LOGGER.info(
        "基础数据缓存：%s｜复用 %d 个｜下载解析 %d 个｜复用不可读记录 %d 个｜当前有效零件 %d 条",
        channel_name,
        reused,
        downloaded,
        cached_errors,
        len(source_parts),
    )
    return source_parts
