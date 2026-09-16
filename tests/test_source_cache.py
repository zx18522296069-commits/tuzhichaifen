from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from models import SourcePart
from source_cache import load_channel_cached


FOLDER_MIME = "application/vnd.google-apps.folder"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class FakeDrive:
    def __init__(self) -> None:
        self.folder = {"id": "order-folder", "name": "157.26-07-11  TEST", "mimeType": FOLDER_MIME}
        self.workbook = {
            "id": "wb-1",
            "name": "TEST 汇总表.xlsx",
            "mimeType": XLSX_MIME,
            "modifiedTime": "2026-09-16T00:00:00Z",
            "size": "1234",
        }
        self.present = True
        self.downloads = 0

    def list_children(self, parent_id: str):
        if parent_id == "channel":
            return [self.folder]
        if parent_id == "order-folder":
            return [self.workbook] if self.present else []
        return []

    def effective_folder_id(self, item: dict):
        return item["id"] if item.get("mimeType") == FOLDER_MIME else None

    def download(self, file_id: str, target: Path) -> None:
        self.downloads += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake workbook")


class SourceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.part = SourcePart(
            order_no="TEST-1000-0916",
            drawing_no="1001-01",
            thickness=20.0,
            base_quantity=2,
            bevel="P",
            total_weight_kg=100.0,
            source_path="王振海/正在加工/157.26-07-11  TEST",
            source_workbook="TEST 汇总表.xlsx",
        )

    def test_unchanged_workbook_reuses_parsed_cache(self) -> None:
        drive = FakeDrive()
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            workdir = root / "work"
            cache_root = root / "cache"
            with patch("source_cache.read_summary_workbook", return_value=[self.part]) as reader:
                first = load_channel_cached(drive, "channel", "正在加工", workdir, cache_root)
                second = load_channel_cached(drive, "channel", "正在加工", workdir, cache_root)

            self.assertEqual(first, [self.part])
            self.assertEqual(second, [self.part])
            self.assertEqual(drive.downloads, 1)
            self.assertEqual(reader.call_count, 1)

    def test_modified_workbook_is_downloaded_and_reparsed(self) -> None:
        drive = FakeDrive()
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            workdir = root / "work"
            cache_root = root / "cache"
            with patch("source_cache.read_summary_workbook", return_value=[self.part]) as reader:
                load_channel_cached(drive, "channel", "正在加工", workdir, cache_root)
                drive.workbook["modifiedTime"] = "2026-09-16T01:00:00Z"
                load_channel_cached(drive, "channel", "正在加工", workdir, cache_root)

            self.assertEqual(drive.downloads, 2)
            self.assertEqual(reader.call_count, 2)

    def test_deleted_workbook_is_not_restored_from_cache(self) -> None:
        drive = FakeDrive()
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            workdir = root / "work"
            cache_root = root / "cache"
            with patch("source_cache.read_summary_workbook", return_value=[self.part]):
                first = load_channel_cached(drive, "channel", "正在加工", workdir, cache_root)
                drive.present = False
                second = load_channel_cached(drive, "channel", "正在加工", workdir, cache_root)

            self.assertEqual(first, [self.part])
            self.assertEqual(second, [])
            self.assertEqual(drive.downloads, 1)


if __name__ == "__main__":
    unittest.main()
