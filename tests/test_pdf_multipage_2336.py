from pathlib import Path
import tempfile
import unittest

from pipeline import _parse_pdf_by_page


PAGE_1 = """
19.6:1
10:42:30 2026/9/12
9250. x2220.毫米
42.087 米
58.
1/
Q235B
N211
40.毫米
98.399 米
0 米
4275.7kg
FastNEST7 7.2
零件索引
1 D53K-1600A-0911 3.2-1-11 T40 2J P x 2
2 D53K-1600A-0911 3.2-1-1 T40 4J P x 4
3 JYT27-1600E-0910 1001-03-03 T40 2J P x 2
4 JYT27-1600E-0910 1001-03-05 T40 4J P x 4
5 JYT27-1600E-0910 1001-03-02-1 T40 4J P x 4
"""

PAGE_2 = """
零件序号连续
6 JYT27-1600E-0910 1001-03-02-2 T40 4J P x 4
7 JYT27-1600E-0910 1001-04-03 T40 6J P x 6
8 D53K-1600A-0911 3.2-4-1 T40 1J W x 1
9 D53K-1600A-0911 3.2-2-1 T40 1J W x 1
10 THP11-6000L-0910 1007-01-17 T40 2J P x 2
11 THP11-6000L-0910 1007-02-17 T40 2J P x 2
12 JYT27-1600E-0910 1001-04-04 T40 4J P x 4
13 THP11-6000L-0910 1001-01-25 T40 3J P x 3
14 JYT27-1600E-0910 1001-04-06 T40 6J P x 6
15 D53K-1600A-0911 3.2-1-8 T40 2J P1F x 2
16 D53K-1600A-0911 3.2-2-7 T40 1J W x 1
17 D53K-1600A-0911 3.2-1-5 T40 2J P x 2
"""


class PdfMultipage2336RegressionTests(unittest.TestCase):
    def test_2336_two_native_pages_merge_without_ocr(self):
        # This regression reproduces the real #2336 two-page FastNEST PDF
        # structure without committing the customer's source PDF itself.
        with tempfile.TemporaryDirectory() as temp_name:
            parsed, ocr_pages = _parse_pdf_by_page(
                "#2336 T40退2220X2520.pdf",
                Path(temp_name) / "not-needed-because-native-text-is-complete.pdf",
                [PAGE_1, PAGE_2],
                Path(temp_name) / "rendered",
            )

        self.assertEqual(ocr_pages, [])
        self.assertEqual(parsed.program_no, "N211")
        self.assertAlmostEqual(parsed.marked_weight_kg, 4275.7)
        self.assertEqual(len(parsed.parts), 17)
        self.assertEqual([part.index for part in parsed.parts], list(range(1, 18)))
        self.assertEqual(parsed.parts[0].drawing_no, "3.2-1-11")
        self.assertEqual(parsed.parts[4].drawing_no, "1001-03-02-1")
        self.assertEqual(parsed.parts[5].drawing_no, "1001-03-02-2")
        self.assertEqual(parsed.parts[-1].drawing_no, "3.2-1-5")
        self.assertEqual(parsed.parts[14].bevel, "P1F")


if __name__ == "__main__":
    unittest.main()
