from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import _parse_pdf_page_parts, _parse_pdf_by_page


class NativeDotOrderRegressionTests(unittest.TestCase):
    def test_real_dotted_order_signature_is_not_anonymous(self) -> None:
        # FastNEST 2026-11-14 failure group (#2384/#1136/#2383/#1133):
        # a dot in an order number must NOT silently downgrade the row
        # into an anonymous T60/W/4J or T30/P/1J part.
        rows = (
            "1 D53K-8000D.4-1104 5.9-1-04 T60 4J W x 4\n"
            "8 D53K-8000D.4-1104 5.1-8-05 T80 2J P x 2\n"
            "10 D53K-8000D.4-1104 4.2-14-07 T30 1J P x 1\n"
            "24 D53K-8000D.4-1104 4.1-25-03 T80 2J P x 1"
        )
        parts = _parse_pdf_page_parts(rows, allow_gaps=True)
        self.assertEqual([part.index for part in parts], [1, 8, 10, 24])
        self.assertTrue(all(part.order_no == "D53K-8000D.4-1104" for part in parts))
        self.assertEqual(
            [part.drawing_no for part in parts],
            ["5.9-1-04", "5.1-8-05", "4.2-14-07", "4.1-25-03"],
        )

    def test_full_38_native_rows_skip_ocr(self) -> None:
        # Synthetic ordering, using the same FastNEST PDF list syntax;
        # tests native text extraction without publishing a customer PDF.
        first = "\n".join(
            f"{index} YT27-1600BP-1028 1004-{index:02d} T30 4J P x 4"
            for index in range(1, 6)
        )
        remainder = "\n".join(
            f"{index} D53K-8000D.4-1104 4.2-12-{index:02d} T30 1J P x 1"
            for index in range(6, 39)
        )
        with tempfile.TemporaryDirectory() as name:
            with patch("pipeline.render_pdf_page") as render:
                result, ocr_pages = _parse_pdf_by_page(
                    "#2383 T30退0.pdf",
                    Path(name) / "unused.pdf",
                    ["5519.68kg\n零件索引\n" + first, "零件序号连续\n" + remainder],
                    Path(name) / "rendered",
                )
        render.assert_not_called()
        self.assertEqual(ocr_pages, [])
        self.assertEqual(len(result.parts), 38)
        self.assertEqual(result.parts[-1].drawing_no, "4.2-12-38")
        self.assertEqual(result.parts[-1].order_no, "D53K-8000D.4-1104")

    def test_last_row_with_fastnest_continue_suffix(self) -> None:
        parts = _parse_pdf_page_parts(
            "1 D53K-8000D.4-1104 5.9-1-04 T60 4J W x 4零件序号连续"
        )
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].drawing_no, "5.9-1-04")


if __name__ == "__main__":
    unittest.main()
