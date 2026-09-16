from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from image_parser import _horizontal_rule_y


class ImageLayoutTests(unittest.TestCase):
    def test_title_block_top_just_above_half_height_is_detected(self) -> None:
        image = Image.new("L", (1000, 1000), 255)
        draw = ImageDraw.Draw(image)
        # 真实 #2333 的标题栏顶边约在图片高度 49.4%；旧逻辑从 50% 才开始找，
        # 会错过顶边并误抓下方标题栏底边。
        draw.line((0, 494, 999, 494), fill=0, width=1)
        draw.line((0, 690, 999, 690), fill=0, width=1)
        self.assertEqual(_horizontal_rule_y(image), 494)


if __name__ == "__main__":
    unittest.main()
