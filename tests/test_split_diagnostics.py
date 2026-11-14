from __future__ import annotations

import unittest

from models import ImageData, ImagePart, SourcePart
from split_diagnostics import inspect_split


class SplitDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.order = "THP34-400J"
        self.folder = "王振海/正在加工/订单"
        self.book = "订单汇总表.xlsx"

    def source(self, drawing: str, *, bevel: str = "P1F", row: int = 12) -> SourcePart:
        return SourcePart(self.order, drawing, 20, 4, bevel, 200.0, self.folder, self.book, row)

    def image(self, parts: tuple[ImagePart, ...]) -> ImageData:
        return ImageData("#2488 T20.pdf", "#2488", "", 100.0, parts, "")

    def test_reports_whole_drawing_segment_and_excel_row(self) -> None:
        item = ImagePart(6, self.order, "1004-0B", 20, 4, "P1F", 2)
        report = inspect_split(self.image((item,)), [self.source("1004-08")], [])
        row = report["rows"][0]
        self.assertEqual(row["status"], "不一致")
        self.assertEqual(row["index"], 6)
        self.assertEqual(row["source"]["row"], 12)
        self.assertEqual(row["differences"], [{
            "field": "drawing_no", "label": "图号",
            "actual": "1004-0B", "expected": "1004-08",
        }])
        self.assertNotIn("characters", row["differences"][0])

    def test_all_parts_checked_and_only_problem_segments_reported(self) -> None:
        image = self.image((
            ImagePart(1, self.order, "1004-01", 20, 4, "P1F", 2),
            ImagePart(2, self.order, "1004-02", 20, 4, "P2F", 2),
            ImagePart(3, self.order, "1004-03", 20, 4, "P1F", 2),
        ))
        sources = [
            self.source("1004-01", row=10),
            self.source("1004-02", row=11),
            self.source("1004-03", row=12),
        ]
        report = inspect_split(image, sources, [])
        self.assertEqual(report["checked_count"], 3)
        self.assertEqual(report["ok_count"], 2)
        self.assertEqual(report["error_count"], 1)
        self.assertEqual([r["status"] for r in report["rows"]], ["通过", "不一致", "通过"])
        self.assertEqual(report["rows"][1]["differences"][0]["label"], "坡口")
        self.assertEqual(report["rows"][1]["differences"][0]["actual"], "P2F")
        self.assertEqual(report["rows"][1]["differences"][0]["expected"], "P1F")

    def test_ambiguity_is_not_a_guessed_match(self) -> None:
        part = ImagePart(7, self.order, "1004-0B", 20, 4, "P1F", 1)
        report = inspect_split(
            self.image((part,)),
            [self.source("1004-08", row=8), self.source("1004-09", row=9)],
            [],
        )
        row = report["rows"][0]
        self.assertEqual(row["status"], "待核对")
        self.assertIsNone(row["source"])
        self.assertEqual(len(row["alternatives"]), 2)
        self.assertEqual(row["differences"], [])


if __name__ == "__main__":
    unittest.main()
