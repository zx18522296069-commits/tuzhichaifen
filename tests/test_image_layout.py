from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from image_parser import _combine_ocr_segments, _horizontal_rule_y, _parse_parts_from_variants


class ImageLayoutTests(unittest.TestCase):
    def test_title_block_top_just_above_half_height_is_detected(self) -> None:
        image = Image.new("L", (1000, 1000), 255)
        draw = ImageDraw.Draw(image)
        # 真实 #2333 的标题栏顶边约在图片高度 49.4%；旧逻辑从 50% 才开始找，
        # 会错过顶边并误抓下方标题栏底边。
        draw.line((0, 494, 999, 494), fill=0, width=1)
        draw.line((0, 690, 999, 690), fill=0, width=1)
        self.assertEqual(_horizontal_rule_y(image), 494)

    def test_main_list_and_continuation_are_rejoined_before_strict_parse(self) -> None:
        main = [
            "1 JYT27-1600E-0910 1001-02-01 T60 1J W x1\n"
            "2 JYT27-1600E-0910 1001-04-01 T60 2J W x2",
            "1 JYT27-1600E-0910 1001-02-01 T60 1J W x1\n"
            "2 JYT27-1600E-0910 1001-04-01 T60 2J W x2",
        ]
        continuation = [
            "3 JYT27-1600E-0910 1001-04-05 T60 2J W x2\n"
            "4 JYT27-1600E-0910 1001-03-12 T60 1J W x1",
            "3 JYT27-1600E-0910 1001-04-05 T60 2J W x2\n"
            "4 JYT27-1600E-0910 1001-03-12 T60 1J W x1",
        ]
        combined = _combine_ocr_segments(main, continuation)
        parts = _parse_parts_from_variants(combined)
        self.assertEqual([part.index for part in parts], [1, 2, 3, 4])
        self.assertEqual(parts[-1].drawing_no, "1001-03-12")


if __name__ == "__main__":
    unittest.main()
