import unittest

from cimb import annotate_cimb_counterparties
from party_utils import clean_counterparty_name, deduplicate_counterparty_names


class CounterpartyCleaningTests(unittest.TestCase):
    def test_removes_person_connectors_and_payroll_noise(self):
        self.assertEqual(clean_counterparty_name("DAVID ANAK RICHARD STAFF"), "DAVID RICHARD")
        self.assertEqual(clean_counterparty_name("DAVID ANAK RICHARD STAFF OVERTIME"), "DAVID RICHARD")
        self.assertEqual(clean_counterparty_name("KHAIRUL OTHMAN BIN"), "KHAIRUL OTHMAN")
        self.assertEqual(clean_counterparty_name("KHAIRUL OTHMAN BIN STAFF OVERTIME"), "KHAIRUL OTHMAN")

    def test_removes_channel_suffixes_from_abbreviations(self):
        names = ["CTC", "CTC CA", "CTC X", "CTC SST"]
        self.assertEqual([clean_counterparty_name(name) for name in names], ["CTC"] * 4)

    def test_removes_month_invoice_prefixes_and_trailing_orphan(self):
        self.assertEqual(
            clean_counterparty_name("JAN INVOICES SAKURA FERROALLOYS S"),
            "SAKURA FERROALLOYS",
        )
        self.assertEqual(
            clean_counterparty_name("SEP INVOICES SAKURA FERROALLOYS S"),
            "SAKURA FERROALLOYS",
        )

    def test_fuzzy_deduplicates_truncated_person_name(self):
        cleaned = deduplicate_counterparty_names(
            ["FATHIN SYAIRAH NAJL", "BALANCE FATHIN SYAIRAH NAJLA"]
        )
        self.assertEqual(cleaned[0], cleaned[1])
        self.assertEqual(cleaned[0], "FATHIN SYAIRAH NAJLA")

    def test_cimb_rows_keep_raw_and_clean_counterparty_fields(self):
        rows = [
            {"description": "TR IBG DAVID ANAK RICHARD STAFF", "party_name": "DAVID ANAK RICHARD STAFF"},
            {"description": "TR IBG DAVID ANAK RICHARD STAFF OVERTIME", "party_name": "DAVID ANAK RICHARD STAFF OVERTIME"},
        ]
        annotate_cimb_counterparties(rows)
        self.assertEqual(rows[0]["counterparty_name_raw"], "DAVID ANAK RICHARD STAFF")
        self.assertEqual(rows[0]["counterparty_name_clean"], "DAVID RICHARD")
        self.assertEqual(rows[1]["counterparty_name_clean"], "DAVID RICHARD")
        self.assertEqual(rows[0]["party_name"], "DAVID RICHARD")

    def test_cimb_other_transfer_fee_uses_fee_category_not_description(self):
        rows = [{"description": "OTHER TRANSFER FEE FATHIN SYAIRAH NAJLA", "party_name": "FATHIN SYAIRAH NAJLA"}]
        annotate_cimb_counterparties(rows)
        self.assertEqual(rows[0]["category"], "TRANSFER FEE")
        self.assertEqual(rows[0]["counterparty_name_raw"], "TRANSFER FEE")
        self.assertEqual(rows[0]["counterparty_name_clean"], "TRANSFER FEE")
        self.assertEqual(rows[0]["party_name"], "TRANSFER FEE")


if __name__ == "__main__":
    unittest.main()
