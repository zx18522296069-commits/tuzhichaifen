import unittest

from image_parser import _parse_parts
from models import ImagePart


class NumericDrawingTests(unittest.TestCase):
    def test_numeric_drawing_number_2310(self):
        parts = _parse_parts("1 THP10-8000J-0911 2310 T80 4J W x 4")
        self.assertEqual(
            parts,
            (
                ImagePart(
                    index=1,
                    order_no="THP10-8000J-0911",
                    drawing_no="2310",
                    thickness=80.0,
                    base_quantity=4,
                    bevel="W",
                    split_quantity=4,
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()
