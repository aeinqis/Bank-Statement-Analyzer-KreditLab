import unittest

from kredit_lab_classify_track2 import (
    _is_excluded_related_party_name,
    advisory_rp_candidates,
    build_track2_result,
    dedup_counterparty_entries,
    scan_related_party_candidates,
)


def _debit(date, description, amount):
    return {
        "date": date,
        "description": description,
        "amount": amount,
        "type": "DEBIT",
    }


def _debit_alias(date, description, amount):
    return {
        "date": date,
        "description": description,
        "amount": -amount,
        "transaction_type": "DR",
    }


def _debit_numeric_only(date, description, amount):
    return {
        "transaction_date": date,
        "description": description,
        "debit": amount,
    }


def _debit_withdrawal(date, description, amount):
    return {
        "transaction_date": date,
        "description": description,
        "type": "DR",
        "withdrawal": f"RM {amount:,.2f}",
    }


class RelatedPartyCandidateTests(unittest.TestCase):
    def test_samsi_ibrahim_round_debits_upgrade_recurrence_to_medium(self):
        samsi = {
            "party_name": "SAMSI IBRAHIM",
            "transaction_count": 6,
            "debit_tx_count": 6,
            "credit_tx_count": 0,
            "total_credit": 0.0,
            "total_debit": 43702.0,
            "transactions": [
                _debit_alias("10/09/2025", "TR TO SAMSI IBRAHIM PROJECT FLOAT", 10000.0),
                _debit_alias("11/10/2025", "TR TO SAMSI IBRAHIM SITE ADVANCE", 5234.0),
                _debit_alias("12/11/2025", "TR TO SAMSI IBRAHIM", 8702.0),
                _debit_alias("13/12/2025", "TR TO SAMSI IBRAHIM", 5000.0),
                _debit_alias("14/01/2026", "TR TO SAMSI IBRAHIM", 6741.0),
                _debit_alias("15/02/2026", "TR TO SAMSI IBRAHIM", 8025.0),
            ],
        }
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "total_debit": 1_000_000.0,
            "transactions": [],
        }

        candidates = scan_related_party_candidates(
            {"counterparties": [samsi, gross_dr_anchor]}
        )
        candidate = next(c for c in candidates if c["name"] == "SAMSI IBRAHIM")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("monthly_recurrence", candidate["signals"])
        self.assertIn("round_amount_advance", candidate["signals"])
        self.assertIn("DR over 6 months", candidate["evidence"])
        self.assertIn("2 round DRs", candidate["evidence"])

    def test_round_debits_detect_from_withdrawal_amount_aliases(self):
        samsi = {
            "party_name": "SAMSI IBRAHIM",
            "transaction_count": 6,
            "debit_tx_count": 6,
            "credit_tx_count": 0,
            "total_credit": 0.0,
            "total_debit": 43702.0,
            "transactions": [
                _debit_withdrawal("2025-09-10", "TR TO SAMSI IBRAHIM PROJECT FLOAT", 10000.0),
                _debit_withdrawal("2025-10-11", "TR TO SAMSI IBRAHIM SITE ADVANCE", 5234.0),
                _debit_withdrawal("2025-11-12", "TR TO SAMSI IBRAHIM", 8702.0),
                _debit_withdrawal("2025-12-13", "TR TO SAMSI IBRAHIM", 5000.0),
                _debit_withdrawal("2026-01-14", "TR TO SAMSI IBRAHIM", 6741.0),
                _debit_withdrawal("2026-02-15", "TR TO SAMSI IBRAHIM", 8025.0),
            ],
        }
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "total_debit": 1_000_000.0,
            "transactions": [],
        }

        candidates = scan_related_party_candidates(
            {"counterparties": [samsi, gross_dr_anchor]}
        )
        candidate = next(c for c in candidates if c["name"] == "SAMSI IBRAHIM")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("round_amount_advance", candidate["signals"])
        self.assertIn("DR over 6 months", candidate["evidence"])
        self.assertIn("2 round DRs", candidate["evidence"])

    def test_fragmented_samsi_purpose_buckets_merge_before_round_scoring(self):
        entries = [
            {
                "counterparty_name": name,
                "transaction_count": 1,
                "debit_count": 1,
                "credit_count": 0,
                "total_credits": 0.0,
                "total_debits": amount,
                "transactions": [
                    _debit(date, f"TR TO C/A {name}", amount),
                ],
            }
            for date, name, amount in [
                ("2025-09-02", "SAMSI IBRAHIM HP", 2058.0),
                ("2025-10-07", "SAMSI IBRAHIM", 4481.0),
                ("2025-11-07", "SAMSI IBRAHIM", 1950.0),
                ("2025-12-14", "SAMSI IBRAHIM", 6741.0),
                ("2026-01-30", "SAMSI IBRAHIM PETTY CASH", 1000.0),
                ("2026-02-19", "SAMSI IBRAHIM DIRECTOR", 5000.0),
            ]
        ]
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "total_debits": 1_000_000.0,
            "transactions": [],
        }

        candidates = scan_related_party_candidates(
            {"counterparties": dedup_counterparty_entries(entries) + [gross_dr_anchor]}
        )
        candidate = next(c for c in candidates if c["name"] == "SAMSI IBRAHIM")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("DR over 6 months", candidate["evidence"])
        self.assertIn("2 round DRs", candidate["evidence"])

    def test_mariana_ahmat_surfaces_from_alias_totals_and_personal_keywords(self):
        mariana = {
            "counterparty": "MARIANA AHMAT",
            "transaction_count": 7,
            "debit_tx_count": 7,
            "credit_tx_count": 0,
            "total_credit": 0.0,
            "total_debit": 9100.0,
            "transactions": [
                _debit_numeric_only("2025-09-05", "TR TO MARIANA AHMAT PETTY CASH", 1200.0),
                _debit_numeric_only("2025-09-26", "TR TO MARIANA AHMAT CLAIM", 800.0),
                _debit_numeric_only("2025-10-07", "TR TO MARIANA AHMAT REIMBURSE", 1500.0),
                _debit_numeric_only("2025-11-08", "TR TO MARIANA AHMAT MEDICAL", 700.0),
                _debit_numeric_only("2025-12-09", "TR TO MARIANA AHMAT BONUS", 1800.0),
                _debit_numeric_only("2026-01-10", "TR TO MARIANA AHMAT CLAIM", 1400.0),
                _debit_numeric_only("2026-01-20", "TR TO MARIANA AHMAT PETTY", 1700.0),
            ],
        }
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "total_debit": 1_000_000.0,
            "transactions": [],
        }

        candidates = scan_related_party_candidates(
            {"counterparties": [mariana, gross_dr_anchor]}
        )
        candidate = next(c for c in candidates if c["name"] == "MARIANA AHMAT")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("personal_keyword_sweep", candidate["signals"])
        self.assertIn("monthly_recurrence", candidate["signals"])
        self.assertIn("7 personal-kw rows", candidate["evidence"])
        self.assertIn("DR over 5 months", candidate["evidence"])
        self.assertEqual(
            advisory_rp_candidates(candidates, effective_related_parties=[])[0]["name"],
            "MARIANA AHMAT",
        )

    def test_mariana_ahmat_screenshot_pattern_surfaces_as_possible_related_party(self):
        mariana = {
            "counterparty_name": "MARIANA AHMAT",
            "transaction_count": 9,
            "credit_count": 0,
            "debit_count": 9,
            "total_credits": 0.0,
            "total_debits": 8760.0,
            "transactions": [
                _debit("2025-09-24", "TR TO SAVINGS MARIANA BINTI AHMAT Petty Cash PO Rahman", 200.0),
                _debit("2025-09-25", "TR TO SAVINGS ACC NO 210316929501 MARIANA BINTI AHMAT SESCO ELECTRICITY", 960.0),
                _debit("2025-11-03", "TR TO SAVINGS STAFF OUTSTATION TO MARIANA BINTI AHMAT PETTY CASH", 500.0),
                _debit("2025-11-13", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 400.0),
                _debit("2025-11-20", "TR TO SAVINGS MARIANA BINTI AHMAT Travel Agent Golf", 5400.0),
                _debit("2025-12-02", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 200.0),
                _debit("2025-12-19", "TR TO SAVINGS MARIANA BINTI AHMAT STAFF BONUS", 500.0),
                _debit("2026-01-28", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 300.0),
                _debit("2026-02-24", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 300.0),
            ],
        }
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "total_debits": 1_000_000.0,
            "transactions": [],
        }

        candidates = scan_related_party_candidates(
            {"counterparties": [mariana, gross_dr_anchor]}
        )
        candidate = next(c for c in candidates if c["name"] == "MARIANA AHMAT")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("personal_keyword_sweep", candidate["signals"])
        self.assertIn("monthly_recurrence", candidate["signals"])
        self.assertIn("7 personal-kw rows", candidate["evidence"])
        self.assertIn("DR over 5 months", candidate["evidence"])
        self.assertEqual(
            advisory_rp_candidates(candidates, effective_related_parties=[])[0]["name"],
            "MARIANA AHMAT",
        )

    def test_mariana_ahmat_stays_possible_when_debit_share_is_high(self):
        mariana = {
            "counterparty_name": "MARIANA AHMAT",
            "transaction_count": 9,
            "credit_count": 0,
            "debit_count": 9,
            "total_credits": 0.0,
            "total_debits": 8760.0,
            "transactions": [
                _debit("2025-09-24", "TR TO SAVINGS MARIANA BINTI AHMAT Petty Cash PO Rahman", 200.0),
                _debit("2025-09-25", "TR TO SAVINGS ACC NO 210316929501 MARIANA BINTI AHMAT SESCO ELECTRICITY", 960.0),
                _debit("2025-11-03", "TR TO SAVINGS STAFF OUTSTATION TO MARIANA BINTI AHMAT PETTY CASH", 500.0),
                _debit("2025-11-13", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 400.0),
                _debit("2025-11-20", "TR TO SAVINGS MARIANA BINTI AHMAT Travel Agent Golf", 5400.0),
                _debit("2025-12-02", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 200.0),
                _debit("2025-12-19", "TR TO SAVINGS MARIANA BINTI AHMAT STAFF BONUS", 500.0),
                _debit("2026-01-28", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 300.0),
                _debit("2026-02-24", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 300.0),
            ],
        }

        candidates = scan_related_party_candidates({"counterparties": [mariana]})
        candidate = next(c for c in candidates if c["name"] == "MARIANA AHMAT")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("personal_keyword_sweep", candidate["signals"])
        self.assertIn("concentration_dr", candidate["signals"])
        self.assertIn("monthly_recurrence", candidate["signals"])
        self.assertEqual(
            advisory_rp_candidates(candidates, effective_related_parties=[])[0]["name"],
            "MARIANA AHMAT",
        )

    def test_track2_report_info_preserves_mariana_candidate_signals(self):
        mariana = {
            "counterparty_name": "MARIANA AHMAT",
            "transaction_count": 9,
            "credit_count": 0,
            "debit_count": 9,
            "total_credits": 0.0,
            "total_debits": 8760.0,
            "transactions": [
                _debit("2025-09-24", "TR TO SAVINGS MARIANA BINTI AHMAT Petty Cash PO Rahman", 200.0),
                _debit("2025-09-25", "TR TO SAVINGS ACC NO 210316929501 MARIANA BINTI AHMAT SESCO ELECTRICITY", 960.0),
                _debit("2025-11-03", "TR TO SAVINGS STAFF OUTSTATION TO MARIANA BINTI AHMAT PETTY CASH", 500.0),
                _debit("2025-11-13", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 400.0),
                _debit("2025-11-20", "TR TO SAVINGS MARIANA BINTI AHMAT Travel Agent Golf", 5400.0),
                _debit("2025-12-02", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 200.0),
                _debit("2025-12-19", "TR TO SAVINGS MARIANA BINTI AHMAT STAFF BONUS", 500.0),
                _debit("2026-01-28", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 300.0),
                _debit("2026-02-24", "TR TO SAVINGS MARIANA BINTI AHMAT PETTY CASH", 300.0),
            ],
        }
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "total_debits": 1_000_000.0,
            "transactions": [],
        }

        report = build_track2_result(
            transactions=[],
            counterparty_ledger={"counterparties": [mariana, gross_dr_anchor]},
            company_names=["BETA TRADING SDN BHD"],
        )
        candidate = next(
            c for c in report["report_info"]["related_party_candidates"]
            if c["name"] == "MARIANA AHMAT"
        )

        self.assertIn("personal_keyword_sweep", candidate["signals"])
        self.assertIn("monthly_recurrence", candidate["signals"])
        self.assertEqual(candidate["debit_month_count"], 5)
        self.assertIn("7 personal-kw rows", candidate["evidence"])

    def test_personal_keyword_sweep_reads_description_and_counterparty_fields(self):
        ahmad = {
            "counterparty_name": "AHMAD ALI",
            "transaction_count": 2,
            "credit_count": 0,
            "debit_count": 2,
            "total_credits": 0.0,
            "total_debits": 4500.0,
            "transactions": [
                {
                    "date": "2025-09-05",
                    "description": "TR TO AHMAD ALI",
                    "counterparty_name_raw": "AHMAD ALI CLAIM",
                    "counterparty_name_clean": "AHMAD ALI",
                    "amount": 2500.0,
                    "type": "DEBIT",
                },
                {
                    "date": "2025-09-26",
                    "description": "DUITNOW TO ACCOUNT AHMAD ALI",
                    "transaction_details": "PETTY CASH AHMAD ALI",
                    "party_name": "AHMAD ALI",
                    "amount": 2000.0,
                    "type": "DEBIT",
                },
            ],
        }
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "total_debits": 1_000_000.0,
            "transactions": [],
        }

        candidates = scan_related_party_candidates(
            {"counterparties": [ahmad, gross_dr_anchor]}
        )
        candidate = next(c for c in candidates if c["name"] == "AHMAD ALI")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("personal_keyword_sweep", candidate["signals"])
        self.assertIn("2 personal-kw rows", candidate["evidence"])
        self.assertEqual(
            advisory_rp_candidates(candidates, effective_related_parties=[])[0]["name"],
            "AHMAD ALI",
        )

    def test_personal_keyword_sweep_reads_full_description_fields(self):
        ahmad = {
            "counterparty_name": "AHMAD ALI",
            "transaction_count": 2,
            "credit_count": 0,
            "debit_count": 2,
            "total_credits": 0.0,
            "total_debits": 4500.0,
            "transactions": [
                {
                    "date": "2025-09-05",
                    "description": "TR TO AHMAD ALI",
                    "raw_description": "TR TO AHMAD ALI CLAIM",
                    "counterparty_name_clean": "AHMAD ALI",
                    "amount": 2500.0,
                    "type": "DEBIT",
                },
                {
                    "date": "2025-09-26",
                    "description": "DUITNOW TO ACCOUNT AHMAD ALI",
                    "transaction_description": "DUITNOW TO ACCOUNT AHMAD ALI PETTY CASH",
                    "party_name": "AHMAD ALI",
                    "amount": 2000.0,
                    "type": "DEBIT",
                },
            ],
        }
        gross_dr_anchor = {
            "counterparty_name": "BETA TRADING SDN BHD",
            "total_debits": 1_000_000.0,
            "transactions": [],
        }

        candidates = scan_related_party_candidates(
            {"counterparties": [ahmad, gross_dr_anchor]}
        )
        candidate = next(c for c in candidates if c["name"] == "AHMAD ALI")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("personal_keyword_sweep", candidate["signals"])
        self.assertIn("2 personal-kw rows", candidate["evidence"])

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

    def test_personal_keyword_sweep_rescues_person_from_synthetic_bucket(self):
        ledger = {
            "counterparties": [
                {
                    "counterparty_name": "TRANSFER FEE",
                    "transaction_count": 2,
                    "credit_count": 0,
                    "debit_count": 2,
                    "total_credits": 0.0,
                    "total_debits": 4500.0,
                    "transactions": [
                        _debit(
                            "2025-09-01",
                            "OTHER TRANSFER FEE FATHIN SYAIRAH NAJLA PETTY CASH",
                            2500.0,
                        ),
                        _debit(
                            "2025-09-26",
                            "OTHER TRANSFER FEE FATHIN SYAIRAH NAJLA CLAIM",
                            2000.0,
                        ),
                    ],
                },
                {
                    "counterparty_name": "BETA TRADING SDN BHD",
                    "total_debits": 1_000_000.0,
                    "transactions": [],
                },
            ],
        }

        candidates = scan_related_party_candidates(ledger)
        candidate = next(c for c in candidates if c["name"] == "FATHIN SYAIRAH NAJLA")

        self.assertEqual(candidate["confidence"], "MEDIUM")
        self.assertIn("personal_keyword_sweep", candidate["signals"])
        self.assertIn("2 personal-kw rows", candidate["evidence"])
        self.assertEqual(
            advisory_rp_candidates(candidates, effective_related_parties=[])[0]["name"],
            "FATHIN SYAIRAH NAJLA",
        )

    def test_transfer_fee_and_full_kwsp_name_are_synthetic_not_related_parties(self):
        ledger = {
            "counterparties": [
                {
                    "counterparty_name": "TRANSFER FEE",
                    "transaction_count": 4,
                    "credit_count": 0,
                    "debit_count": 4,
                    "total_credits": 0.0,
                    "total_debits": 20000.0,
                    "transactions": [
                        _debit("2025-09-01", "OTHER TRANSFER FEE", 5000.0),
                        _debit("2025-10-01", "OTHER TRANSFER FEE", 5000.0),
                        _debit("2025-11-01", "OTHER TRANSFER FEE", 5000.0),
                        _debit("2025-12-01", "OTHER TRANSFER FEE", 5000.0),
                    ],
                },
                {
                    "counterparty_name": "KUMPULAN WANG SIMPANAN PEKERJA",
                    "transaction_count": 4,
                    "credit_count": 0,
                    "debit_count": 4,
                    "total_credits": 0.0,
                    "total_debits": 40000.0,
                    "transactions": [
                        _debit("2025-09-15", "KUMPULAN WANG SIMPANAN PEKERJA", 10000.0),
                        _debit("2025-10-15", "KUMPULAN WANG SIMPANAN PEKERJA", 10000.0),
                        _debit("2025-11-15", "KUMPULAN WANG SIMPANAN PEKERJA", 10000.0),
                        _debit("2025-12-15", "KUMPULAN WANG SIMPANAN PEKERJA", 10000.0),
                    ],
                },
            ]
        }

        self.assertEqual(scan_related_party_candidates(ledger), [])

    def test_synthetic_names_are_excluded_even_from_related_party_overrides(self):
        self.assertTrue(_is_excluded_related_party_name({"name": "TRANSFER FEE"}))
        self.assertTrue(
            _is_excluded_related_party_name(
                {"name": "KUMPULAN WANG SIMPANAN PEKERJA"}
            )
        )
        self.assertFalse(_is_excluded_related_party_name({"name": "DAYANG SITI RAUDZAH"}))


if __name__ == "__main__":
    unittest.main()
