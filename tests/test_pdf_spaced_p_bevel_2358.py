from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_parser import _parse_parts
from pipeline import _parse_pdf_by_page, _parse_pdf_page_parts


PAGE_2358 = """3105.8kg
零件索引
1 THP34-400J 1001-01 T80 4J P 2F x 4
2 D53K-8000D-1012 5.2-1-08 T80 2J P1F x 2
3 THP34-400J 1001-31 T80 2J P x 2
4 THP34-400J 1001-26 T80 2J P x 2
5 D53K-8000D-1016 4.2-1-11 T80 2J P x 2
"""


class SpacedPBevelTests(unittest.TestCase):
    def test_real_2358_pdf_all_rows_without_ocr(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("pipeline.render_pdf_page") as render:
                image, ocr_pages = _parse_pdf_by_page(
                    "#2358 T80退2200X4870.pdf",
                    Path(d) / "unused.pdf",
                    [PAGE_2358],
                    Path(d) / "rendered",
                )
        self.assertEqual(ocr_pages, [])
        render.assert_not_called()
        self.assertEqual([part.index for part in image.parts], [1, 2, 3, 4, 5])
        self.assertEqual(image.parts[0].bevel, "P2F")
        self.assertEqual(image.parts[0].drawing_no, "1001-01")
        self.assertEqual(image.parts[0].base_quantity, 4)
        self.assertEqual(image.parts[0].split_quantity, 4)
        self.assertFalse(image.parts[0].bevel_unmarked)
        self.assertEqual(image.parts[1].bevel, "P1F")
        self.assertEqual(image.marked_weight_kg, 3105.8)

    def test_all_internal_p_whitespace_removed_not_suffix_data(self):
        for bevel in ("P2F", "P 2F", "P2 F", "P 2 F", "P  2  F", "P\t2\tF"):
            with self.subTest(bevel=bevel):
                line = f"1 THP34-400J 1001-01 T80 4J {bevel} x 4"
                native = _parse_pdf_page_parts(line)
                ocr = _parse_parts(line)
                self.assertEqual(native[0].bevel, "P2F")
                self.assertEqual(ocr[0].bevel, "P2F")
                self.assertFalse(native[0].bevel_unmarked)
                self.assertFalse(ocr[0].bevel_unmarked)

    def test_other_bevel_and_missing_bevel_unchanged(self):
        cases = (
            ("P x 4", "P", False),
            ("P1F x 4", "P1F", False),
            ("W x 4", "W", False),
            ("x 4", "W", True),
        )
        for label, bevel, unmarked in cases:
            with self.subTest(label=label):
                line = f"1 THP34-400J 1001-01 T80 4J {label}"
                for parsed in (_parse_pdf_page_parts(line), _parse_parts(line)):
                    self.assertEqual(parsed[0].bevel, bevel)
                    self.assertEqual(parsed[0].bevel_unmarked, unmarked)


if __name__ == "__main__":
    unittest.main()
