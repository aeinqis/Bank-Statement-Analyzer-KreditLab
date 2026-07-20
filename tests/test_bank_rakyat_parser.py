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

    def test_parsed_rows_include_extracted_party_name(self):
        class FakePage:
            def extract_words(self, **kwargs):
                return []

        class FakePdf:
            pages = [FakePage()]

        parsed_row = {
            "date": "2024-12-16",
            "transaction_code": "",
            "description": "DUITNOW TRANSFER AANS MARINE SDN BHD MARINE INV24 063 20PCT",
            "debit": 96000.0,
            "credit": 0.0,
            "balance": -237349.90,
            "page": 1,
        }

        with (
            patch("bank_rakyat.words_to_lines", return_value={}),
            patch("bank_rakyat.find_boundary_ys", return_value=(10.0, None)),
            patch("bank_rakyat.extract_summary_from_lines", return_value={}),
            patch("bank_rakyat.calibrate_columns", return_value={}),
            patch("bank_rakyat.extract_transactions", return_value=[parsed_row]),
        ):
            rows = parse_bank_rakyat(FakePdf(), source_filename="sample.pdf")

        self.assertEqual(rows[0]["party_name"], "AANS MARINE SDN BHD")


if __name__ == "__main__":
    unittest.main()
