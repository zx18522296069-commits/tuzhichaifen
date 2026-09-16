from __future__ import annotations

import unittest

from matcher import MatchError, match_one
from models import ImagePart, SourcePart


class NamedSourceAliasTests(unittest.TestCase):
    def test_lifting_ear_t60_maps_only_to_confirmed_bhdr_master_key(self) -> None:
        image = ImagePart(3, "", "吊耳", 60, 100, "P", 13)
        source = SourcePart(
            "BHDR-0715",
            "1000-02",
            60,
            100,
            "P",
            3082.0,
            "王振海/拆图模版/160.26-07-15  BHDR-0715",
            "BHDR-0715 模板.xlsm",
        )
        self.assertEqual(match_one(image, [source]), source)

    def test_lifting_ear_alias_does_not_apply_to_wrong_master_drawing(self) -> None:
        image = ImagePart(3, "", "吊耳", 60, 100, "P", 13)
        wrong = SourcePart(
            "BHDR-0715",
            "1000-01",
            60,
            100,
            "P",
            3000.0,
            "路径",
            "模板.xlsm",
        )
        with self.assertRaises(MatchError):
            match_one(image, [wrong])

    def test_lifting_ear_alias_is_not_used_for_other_thickness(self) -> None:
        image = ImagePart(3, "", "吊耳", 40, 100, "P", 13)
        source = SourcePart(
            "BHDR-0715",
            "1000-02",
            40,
            100,
            "P",
            1000.0,
            "路径",
            "模板.xlsm",
        )
        with self.assertRaises(MatchError):
            match_one(image, [source])


if __name__ == "__main__":
    unittest.main()
