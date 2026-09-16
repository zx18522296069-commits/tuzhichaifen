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

    def test_main_list_and_continuation_are_cross_rejoined_before_strict_parse(self) -> None:
        # 模拟真实 #2333：主表的最佳 OCR 出现在第一个候选，续表的最佳 OCR
        # 出现在第二个候选。旧版 zip 一一对应时无法形成完整 1..4；交叉组合应能恢复。
        main = [
            "1 JYT27-1600E-0910 1001-02-01 T60 1J W x1\n"
            "2 JYT27-1600E-0910 1001-04-01 T60 2J W x2",
            "OCR NOISE WITHOUT COMPLETE MAIN TABLE",
        ]
        continuation = [
            "OCR NOISE WITHOUT COMPLETE CONTINUATION TABLE",
            "3 JYT27-1600E-0910 1001-04-05 T60 2J W x2\n"
            "4 JYT27-1600E-0910 1001-03-12 T60 1J W x1",
        ]
        combined = _combine_ocr_segments(main, continuation)
        self.assertEqual(len(combined), 4)
        parts = _parse_parts_from_variants(combined)
        self.assertEqual([part.index for part in parts], [1, 2, 3, 4])
        self.assertEqual(parts[-1].drawing_no, "1001-03-12")

    def test_glued_thickness_quantity_and_confirmed_lifting_ear_alias(self) -> None:
        # 真实 #2333 会出现 T604J / T601J 这类粘连，且“吊耳”稳定有一组 OCR 为“帅耳”。
        # 当同长度候选里一组丢了图号、一组识别到已确认别名时，应选择信息更完整的候选。
        incomplete = (
            "1 JYT27-1600E-0910 1001-02-02 T602JPx2\n"
            "2 HE T60100JPx13\n"
            "3 JYT27-1600E-0910 1001-02-07 T601JWx1"
        )
        identified = (
            "1 JYT27-1600E-0910 1001-02-02 T602JPx2\n"
            "2 帅耳 T60100JPx13\n"
            "3 JYT27-1600E-0910 1001-02-07 T601JWx1"
        )
        parts = _parse_parts_from_variants([incomplete, identified])
        self.assertEqual([part.index for part in parts], [1, 2, 3])
        self.assertEqual(parts[0].thickness, 60)
        self.assertEqual(parts[0].base_quantity, 2)
        self.assertEqual(parts[1].drawing_no, "吊耳")
        self.assertEqual(parts[1].base_quantity, 100)


if __name__ == "__main__":
    unittest.main()
