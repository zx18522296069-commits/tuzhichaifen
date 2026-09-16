from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_parser import ImageParseError
from models import ImagePart
from pipeline import _merge_pdf_page_parts, _parse_pdf_by_page, _parse_pdf_page_parts


class PdfPagewiseTests(unittest.TestCase):
    def test_later_page_can_start_from_non_one_index(self) -> None:
        text = """
        4 ORDER-2026-0001 1001-04 T40 2J P x 1
        5 ORDER-2026-0001 1001-05 T40 2J P x 2
        6 ORDER-2026-0001 1001-06 T40 2J W x 2
        """
        parts = _parse_pdf_page_parts(text)
        self.assertEqual([part.index for part in parts], [4, 5, 6])
        self.assertEqual(parts[0].drawing_no, "1001-04")

    def test_multi_page_native_text_merges_without_ocr(self) -> None:
        pages = [
            "重量 100.00kg\n1 ORDER-2026-0001 1001-01 T40 2J P x 1\n2 ORDER-2026-0001 1001-02 T40 2J P x 1",
            "3 ORDER-2026-0001 1001-03 T40 2J W x 2\n4 ORDER-2026-0001 1001-04 T40 2J W x 1",
        ]
        with tempfile.TemporaryDirectory() as name:
            image, ocr_pages = _parse_pdf_by_page(
                "#999 T40.pdf",
                Path(name) / "unused.pdf",
                pages,
                Path(name) / "rendered",
            )
        self.assertEqual(ocr_pages, [])
        self.assertEqual([part.index for part in image.parts], [1, 2, 3, 4])
        self.assertEqual(image.marked_weight_kg, 100.0)

    def test_only_failed_page_is_rendered_for_ocr(self) -> None:
        pages = [
            "重量 100.00kg\n1 ORDER-2026-0001 1001-01 T40 2J P x 1",
            "",  # scanned second page: only this page should enter OCR
        ]
        ocr_part = ImagePart(2, "ORDER-2026-0001", "1001-02", 40, 2, "P", 1)
        with tempfile.TemporaryDirectory() as name:
            rendered = Path(name) / "rendered" / "page-2.png"
            with patch("pipeline.render_pdf_page", return_value=rendered) as render_mock, patch(
                "pipeline._parse_pdf_page_ocr",
                return_value=((ocr_part,), "2 ORDER-2026-0001 1001-02 T40 2J P x 1"),
            ) as ocr_mock:
                image, ocr_pages = _parse_pdf_by_page(
                    "#999 T40.pdf",
                    Path(name) / "source.pdf",
                    pages,
                    Path(name) / "rendered",
                )
        self.assertEqual(ocr_pages, [2])
        self.assertEqual([part.index for part in image.parts], [1, 2])
        render_mock.assert_called_once()
        self.assertEqual(render_mock.call_args.args[2], 1)
        ocr_mock.assert_called_once_with(rendered)

    def test_cross_page_conflict_is_rejected(self) -> None:
        left = (ImagePart(1, "ORDER-2026-0001", "1001-01", 40, 2, "P", 1),)
        right = (ImagePart(1, "ORDER-2026-0001", "1001-99", 40, 2, "P", 1),)
        with self.assertRaisesRegex(ImageParseError, "跨页零件序号 1 内容不一致"):
            _merge_pdf_page_parts([left, right])

    def test_cross_page_index_gap_is_rejected(self) -> None:
        page1 = (ImagePart(1, "ORDER-2026-0001", "1001-01", 40, 2, "P", 1),)
        page2 = (ImagePart(3, "ORDER-2026-0001", "1001-03", 40, 2, "P", 1),)
        with self.assertRaisesRegex(ImageParseError, "跨页零件序号不连续"):
            _merge_pdf_page_parts([page1, page2])


if __name__ == "__main__":
    unittest.main()
