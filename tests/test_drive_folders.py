from __future__ import annotations

import unittest
from unittest.mock import Mock

from drive_client import DriveClient, DriveError


class DriveFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        # These tests exercise folder selection without Google credentials or network calls.
        self.drive = DriveClient.__new__(DriveClient)

    def test_processing_remains_primary_when_template_is_missing(self) -> None:
        processing = {
            "id": "processing-id",
            "name": "正在加工",
            "mimeType": "application/vnd.google-apps.folder",
        }
        self.drive.list_children = Mock(return_value=[processing])

        self.assertEqual(self.drive.find_child_folder("root", "正在加工"), processing)
        self.assertIsNone(self.drive.find_child_folder("root", "拆图模版", required=False))

    def test_missing_required_folder_still_fails(self) -> None:
        self.drive.list_children = Mock(return_value=[])
        with self.assertRaises(DriveError):
            self.drive.find_child_folder("root", "正在加工")

    def test_ambiguous_template_folder_still_fails(self) -> None:
        folders = [
            {"id": "a", "name": "拆图模版", "mimeType": "application/vnd.google-apps.folder"},
            {"id": "b", "name": "总拆图模版", "mimeType": "application/vnd.google-apps.folder"},
        ]
        self.drive.list_children = Mock(return_value=folders)
        with self.assertRaises(DriveError):
            self.drive.find_child_folder("root", "拆图模版", required=False)


if __name__ == "__main__":
    unittest.main()
