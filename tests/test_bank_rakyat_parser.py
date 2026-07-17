import unittest
from unittest.mock import patch

from bank_rakyat import parse_bank_rakyat


class BankRakyatParserTests(unittest.TestCase):
    def test_accepts_already_open_pdfplumber_pdf(self):
        class FakePdf:
            pages = []

        with patch("bank_rakyat.pdfplumber.open") as mocked_open:
            rows = parse_bank_rakyat(FakePdf(), source_filename="sample.pdf")

        self.assertEqual(rows, [])
        mocked_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
