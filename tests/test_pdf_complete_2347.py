from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook
from excel_writer import validate_result, write_result
from image_parser import ImageParseError
from models import ImageData, ImagePart, MatchedPart, SourcePart
from pipeline import _parse_pdf_by_page, _parse_pdf_page_parts

# #2347 original FastNEST text: page 1 has two columns, page 2 continues.
PAGE1 = """1722.9kg
零件索引
1 THP34-400J 1001-12 T20 2J x 2
2 THP34-400J 1004-05 T20 2J P x 2
3 YT25-2000K-1012 1004-18 T20 8J W x 8
4 THP34-400J 1004-07 T20 2J P x 2
5 THP34-400J 1004-08 T20 4J x 4
6 THP34-400J 1004-01 T20 8J x 8
7 YT25-2000K-1012 1004-10 T20 4J W x 4
8 THP34-400J 1004-04 T20 4J x 4
9 D53K-8000D-1012 5.2-1-33 T20 1J W x 1
10 YT25-2000K-1012 1004-30 T20 2J W x 2
11 YT25-2000K-1012 1004-36 T20 8J W x 8零件序号连续
"""
PAGE2 = """零件序号连续
12 YT25-2000K-1012 1004-11 T20 4J W x 4
13 YT25-2000K-1012 1004-16 T20 4J W x 4
14 YT25-2000K-1012 1004-24 T20 4J W x 4
15 YT25-2000K-1012 1004-12 T20 4J W x 4
16 YT25-2000K-1012 1004-29 T20 2J W x 2
17 YT25-2000K-1012 1004-28 T20 2J W x 2
18 YT25-2000K-1012 1004-39 T20 2J W x 2
19 YT25-2000K-1012 1004-43 T20 2J W x 2
20 YT25-2000K-1012 1004-13 T20 4J W x 4
21 YT25-2000K-1012 1004-38 T20 2J W x 2
22 YT25-2000K-1012 1004-17 T20 4J W x 4
"""


class Pdf2347SafetyTests(unittest.TestCase):
    def test_all_22_parts_and_unmarked_bevel(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("pipeline.render_pdf_page") as render:
                image, pages = _parse_pdf_by_page(
                    "#2347 T20退2240X1750.pdf", Path(d) / "ignored.pdf",
                    [PAGE1, PAGE2], Path(d) / "ocr",
                )
        self.assertEqual(pages, [])
        render.assert_not_called()
        self.assertEqual([p.index for p in image.parts], list(range(1, 23)))
        self.assertEqual({p.index for p in image.parts if p.bevel_unmarked}, {1, 5, 6, 8})
        self.assertTrue(all(p.bevel == "W" for p in image.parts if p.bevel_unmarked))
        self.assertEqual(image.parts[5].drawing_no, "1004-01")
        self.assertEqual(image.parts[8].drawing_no, "5.2-1-33")

    def test_native_partial_preserved_by_ocr(self):
        partial = ("100.00kg\n1 ORDER-2026-0001 1001-01 T20 2J x 2\n"
                   "3 ORDER-2026-0001 1001-03 T20 2J P x 1")
        row2 = ImagePart(2, "ORDER-2026-0001", "1001-02", 20, 2, "W", 2, True)
        with tempfile.TemporaryDirectory() as d:
            with patch("pipeline.render_pdf_page", return_value=Path(d) / "2.png"), patch(
                "pipeline._parse_pdf_page_ocr", return_value=((row2,), "100.00kg")
            ):
                image, pages = _parse_pdf_by_page(
                    "#2347.pdf", Path(d) / "ignored.pdf", [partial], Path(d) / "ocr"
                )
        self.assertEqual(pages, [1])
        self.assertEqual([p.index for p in image.parts], [1, 2, 3])
        self.assertTrue(image.parts[0].bevel_unmarked)

    def test_ocr_conflict_blocks_completion(self):
        native = ("100.00kg\n1 ORDER-2026-0001 1001-01 T20 2J P x 2\n"
                  "3 ORDER-2026-0001 1001-03 T20 2J P x 1")
        candidates = (ImagePart(2, "ORDER-2026-0001", "1001-02", 20, 2, "P", 1),
                      ImagePart(3, "ORDER-2026-0001", "1001-03", 20, 2, "W", 1))
        with tempfile.TemporaryDirectory() as d:
            with patch("pipeline.render_pdf_page", return_value=Path(d) / "2.png"), patch(
                "pipeline._parse_pdf_page_ocr", return_value=(candidates, "100.00kg")
            ):
                with self.assertRaisesRegex(ImageParseError, "内容冲突"):
                    _parse_pdf_by_page("#2347.pdf", Path(d) / "x.pdf", [native], Path(d) / "ocr")

    def test_excel_warning_uses_existing_j_column(self):
        part = _parse_pdf_page_parts(PAGE1)[0]
        source = SourcePart("THP34-400J", "1001-12", 20, 2, "W", 30.0, "王振海/正在加工/2347", "2347汇总表.xlsx")
        image = ImageData("#2347.pdf", "#2347", "", 30.0, (part,), PAGE1)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "#2347_完成.xlsx"
            write_result(path, image, (MatchedPart(part, source),))
            validate_result(path, 1)
            sheet = load_workbook(path)["拆图明细"]
            self.assertEqual(sheet["J4"].value, "坡口标注")
            self.assertEqual(sheet["J5"].value, "W（未标注坡口）")


if __name__ == "__main__":
    unittest.main()
