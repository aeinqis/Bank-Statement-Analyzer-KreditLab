import unittest

from kredit_lab_classify_track2 import (
    advisory_rp_candidates,
    scan_related_party_candidates,
)


def _debit(date, description, amount):
    return {
        "date": date,
        "description": description,
        "amount": amount,
        "type": "DEBIT",
    }


class RelatedPartyCandidateTests(unittest.TestCase):
    def test_khairul_othman_surfaces_on_three_plus_debit_months(self):
        khairul = {
            "counterparty_name": "KHAIRUL OTHMAN",
            "transaction_count": 9,
            "credit_count": 0,
            "debit_count": 9,
            "total_credits": 0.0,
            "total_debits": 29035.0,
            "transactions": [
                _debit("2025-09-10", "TR TO SAVINGS KHAIRUL OTHMAN BIN STAFF OVERTIME", 110.0),
                _debit("2025-09-11", "TR TO SAVINGS KHAIRUL OTHMAN BIN DEVICE VMS", 3796.0),
                _debit("2025-11-27", "TR TO SAVINGS HOSPITAL SIBU KHAIRUL OTHMAN BIN PERUNTUKAN BAJET", 4615.0),
                _debit("2025-12-01", "TR TO SAVINGS PENYERAHAN DEVICE UT KHAIRUL OTHMAN BIN PROJEK AIRBUS", 350.0),
                _debit("2025-12-01", "TR TO SAVINGS PETTY CASH KHAIRUL OTHMAN BIN POB MPSB", 2000.0),
                _debit("2025-12-03", "TR TO SAVINGS KHAIRUL OTHMAN BIN POB MTSB", 5000.0),
                _debit("2026-01-02", "TR TO SAVINGS GUARDPRO KHAIRUL OTHMAN BIN DEVICE 2 SET", 678.0),
                _debit("2026-02-03", "TR TO SAVINGS LOGI CAM AND MEMORY KHAIRUL OTHMAN BIN PROJECT AIRBUS", 2486.0),
                _debit("2026-02-23", "TR TO SAVINGS KHAIRUL OTHMAN BIN STAFF INCENTIVE", 10000.0),
            ],
        }
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "transaction_count": 1,
            "credit_count": 0,
            "debit_count": 1,
            "total_credits": 0.0,
            "total_debits": 2_000_000.0,
            "transactions": [
                _debit("2026-02-01", "PAYMENT BETA TRADING SDN BHD", 2_000_000.0),
            ],
        }

        candidates = scan_related_party_candidates(
            {"counterparties": [khairul, gross_dr_anchor]}
        )
        khairul_candidate = next(c for c in candidates if c["name"] == "KHAIRUL OTHMAN")

        self.assertEqual(khairul_candidate["confidence"], "MEDIUM")
        self.assertIn("monthly_recurrence", khairul_candidate["signals"])
        self.assertEqual(khairul_candidate["debit_month_count"], 5)
        self.assertEqual(
            advisory_rp_candidates(candidates, effective_related_parties=[])[0]["name"],
            "KHAIRUL OTHMAN",
        )

    def test_advisory_candidates_prioritise_recurring_debit_month_signal(self):
        candidates = [
            {
                "name": "AHMAD ALI",
                "confidence": "MEDIUM",
                "signals": ["personal_keyword_sweep"],
                "total_dr": 100000.0,
                "total_cr": 0.0,
                "debit_count": 3,
                "credit_count": 0,
                "debit_month_count": 1,
            },
            {
                "name": "KHAIRUL OTHMAN",
                "confidence": "LOW",
                "signals": ["monthly_recurrence"],
                "total_dr": 29035.0,
                "total_cr": 0.0,
                "debit_count": 9,
                "credit_count": 0,
                "debit_month_count": 5,
            },
        ]

        ordered = advisory_rp_candidates(candidates, effective_related_parties=[])

        self.assertEqual([c["name"] for c in ordered], ["KHAIRUL OTHMAN", "AHMAD ALI"])


if __name__ == "__main__":
    unittest.main()
