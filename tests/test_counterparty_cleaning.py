import unittest

from cimb import annotate_cimb_counterparties
from party_utils import (
    build_transactions_by_party,
    clean_counterparty_name,
    deduplicate_counterparty_names,
    normalise_counterparty_for_ledger,
)


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

    def test_preserves_and_expands_company_suffixes(self):
        self.assertEqual(clean_counterparty_name("ALPHA SB"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA SDN BH"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA SDN BHD"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA BERHAD"), "ALPHA BHD")

    def test_ledger_normaliser_strips_statement_holder_tokens(self):
        self.assertEqual(
            normalise_counterparty_for_ledger(
                "NEW GLOBAL SDN BHD ALPHA TRADING",
                own_party="NEWTON GLOBAL SDN BHD",
                description="PAYMENT NEWTON GLOBAL SDN BHD ALPHA TRADING",
            ),
            "ALPHA TRADING",
        )

    def test_build_transactions_by_party_uses_ledger_normalisation(self):
        import pandas as pd

        rows = pd.DataFrame(
            [
                {
                    "date": "2026-01-01",
                    "description": "PAYMENT NEWTON GLOBAL SDN BHD ALPHA TRADING",
                    "party_name": "NEWTON GLOBAL SDN BHD ALPHA TRADING",
                    "company_name": "NEWTON GLOBAL SDN BHD",
                    "credit": 100.0,
                    "debit": 0.0,
                    "source_file": "sample.pdf",
                }
            ]
        )

        party_tables = build_transactions_by_party(rows)
        self.assertEqual(len(party_tables), 1)
        self.assertEqual(party_tables[0]["party"], "ALPHA TRADING")

    def test_fuzzy_deduplicates_truncated_person_name(self):
        cleaned = deduplicate_counterparty_names(
            ["FATHIN SYAIRAH NAJL", "BALANCE FATHIN SYAIRAH NAJLA"]
        )
        self.assertEqual(cleaned[0], cleaned[1])
        self.assertEqual(cleaned[0], "FATHIN SYAIRAH NAJLA")

    def test_merges_person_payment_memo_suffixes(self):
        cleaned = deduplicate_counterparty_names(
            [
                "DAYANG SITI RAUDZAH",
                "DAYANG SITI RAUDZAH CASH",
                "DAYANG SITI RAUDZAH HOUSING LOAN",
                "DAYANG SITI RAUDZAH OFFICE ELECTRICITY",
            ]
        )
        self.assertEqual(cleaned, ["DAYANG SITI RAUDZAH"] * 4)

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
