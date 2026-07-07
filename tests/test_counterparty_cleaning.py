import unittest

from cimb import annotate_cimb_counterparties, extract_cimb_party_name
from kredit_lab_classify_track2 import _build_own_related_transactions_list_track2
from party_utils import (
    _merge_counterparty_groups,
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
        self.assertEqual(clean_counterparty_name("SAMSI BIN IBRAHIM HP MONTHLY"), "SAMSI IBRAHIM")
        self.assertEqual(clean_counterparty_name("SAMSI BIN IBRAHIM PETTY CASH"), "SAMSI IBRAHIM")
        self.assertEqual(clean_counterparty_name("SAMSI BIN IBRAHIM DIRECTOR FEE"), "SAMSI IBRAHIM")

    def test_embedded_khairul_othman_variants_merge(self):
        names = [
            "KHAIRUL OTHMAN BIN POB MTSB",
            "KHAIRUL OTHMAN BIN DEVICE VMS",
            "HOSPITAL SIBU KHAIRUL OTHMAN BIN PERUNTUKAN BAJET",
            "PENYERAHAN DEVICE UT KHAIRUL OTHMAN BIN PROJEK AIRBUS",
            "PETTY CASH KHAIRUL OTHMAN BIN POB MPSB",
            "GUARDPRO KHAIRUL OTHMAN BIN DEVICE 2 SET",
            "LOGI CAM AND MEMORY KHAIRUL OTHMAN BIN PROJECT AIRBUS",
            "KHAIRUL OTHMAN BIN STAFF INCENTIVE",
        ]

        self.assertEqual([clean_counterparty_name(name) for name in names], ["KHAIRUL OTHMAN"] * len(names))

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
        self.assertEqual(clean_counterparty_name("ALPHA MTSB"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA SDN BH"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA SDN BHD"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA BERHAD"), "ALPHA BHD")
        self.assertEqual(clean_counterparty_name("MUHAFIZ PRIMA SDN. B"), "MUHAFIZ PRIMA SDN BHD")

    def test_truncates_company_name_after_sd_or_sdn_marker(self):
        self.assertEqual(clean_counterparty_name("ALPHA SD TOKEN PAYMENT"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA SDN BHD RENTAL JUL"), "ALPHA SDN BHD")

    def test_ibg_credit_counterparty_keeps_company_name(self):
        desc = "IBG CREDIT INTERBANK GIRO INTERBANK GIRO SOUTHERN CABLE SDN B"
        self.assertEqual(extract_cimb_party_name(desc), "SOUTHERN CABLE SDN BHD")
        self.assertEqual(clean_counterparty_name(desc), "SOUTHERN CABLE SDN BHD")

    def test_cimb_person_purpose_suffixes_strip_to_person_name(self):
        self.assertEqual(
            extract_cimb_party_name("TR TO C/A SAMSI BIN IBRAHIM HP MONTHLY"),
            "SAMSI BIN IBRAHIM",
        )
        self.assertEqual(
            extract_cimb_party_name("TR TO C/A SAMSI BIN IBRAHIM DIRECTOR FEE"),
            "SAMSI BIN IBRAHIM",
        )

    def test_counterparty_cleaning_removes_bank_names(self):
        self.assertEqual(clean_counterparty_name("MAYBANK"), "UNKNOWN")
        self.assertEqual(clean_counterparty_name("CIMB BANK AHMAD FIRDAUS"), "AHMAD FIRDAUS")
        self.assertEqual(clean_counterparty_name("BANK ISLAM MALAYSIA BERHAD"), "UNKNOWN")

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
                "& DAYANG SITI RAUDZAH",
                "DAYANG SITI RAUDZAH CASH",
                "DAYANG SITI RAUDZAH HOUSING LOAN",
                "DAYANG SITI RAUDZAH OFFICE ELECTRICITY",
            ]
        )
        self.assertEqual(cleaned, ["DAYANG SITI RAUDZAH"] * 5)

    def test_strips_description_noise_from_person_counterparties(self):
        self.assertEqual(clean_counterparty_name("& DAYANG SITI RAUDZAH"), "DAYANG SITI RAUDZAH")
        self.assertEqual(clean_counterparty_name("SHAHARUDDIN SAMS HOUSE INSTALMENT"), "SHAHARUDDIN SAMS")
        self.assertEqual(clean_counterparty_name("SHAHARUDDIN SAMS INSTALMENT"), "SHAHARUDDIN SAMS")
        self.assertEqual(
            clean_counterparty_name("KETUA UNIT KESELAMAT DAYANG SURIATI BINT FAREWELL"),
            "DAYANG SURIATI",
        )

    def test_ledger_groups_names_after_description_noise_stripping(self):
        import pandas as pd

        rows = pd.DataFrame(
            [
                {
                    "date": "2026-01-01",
                    "description": "TR IBG DAYANG SITI RAUDZAH",
                    "party_name": "DAYANG SITI RAUDZAH",
                    "credit": 0.0,
                    "debit": 100.0,
                    "source_file": "sample.pdf",
                },
                {
                    "date": "2026-01-02",
                    "description": "TR IBG DAYANG SITI RAUDZAH OFFICE ELECTRICITY",
                    "party_name": "DAYANG SITI RAUDZAH OFFICE ELECTRICITY",
                    "credit": 0.0,
                    "debit": 50.0,
                    "source_file": "sample.pdf",
                },
                {
                    "date": "2026-01-03",
                    "description": "TR IBG & DAYANG SITI RAUDZAH",
                    "party_name": "& DAYANG SITI RAUDZAH",
                    "credit": 0.0,
                    "debit": 25.0,
                    "source_file": "sample.pdf",
                },
            ]
        )

        party_tables = build_transactions_by_party(rows)
        self.assertEqual(len(party_tables), 1)
        self.assertEqual(party_tables[0]["party"], "DAYANG SITI RAUDZAH")
        self.assertEqual(party_tables[0]["count"], 3)

    def test_iterative_counterparty_group_merge_preserves_totals(self):
        groups = {
            "FATHIN SYAIRAH NAJL": {
                "counterparty_name": "FATHIN SYAIRAH NAJL",
                "total_credits": 10.0,
                "total_debits": 0.0,
                "credit_count": 1,
                "debit_count": 0,
                "transaction_count": 1,
                "transactions": [{"description": "A"}],
            },
            "FATHIN SYAIRAH NAJLA": {
                "counterparty_name": "FATHIN SYAIRAH NAJLA",
                "total_credits": 0.0,
                "total_debits": 5.0,
                "credit_count": 0,
                "debit_count": 1,
                "transaction_count": 1,
                "transactions": [{"description": "B"}],
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(list(merged.keys()), ["FATHIN SYAIRAH NAJLA"])
        row = merged["FATHIN SYAIRAH NAJLA"]
        self.assertEqual(row["total_credits"], 10.0)
        self.assertEqual(row["total_debits"], 5.0)
        self.assertEqual(row["transaction_count"], 2)
        self.assertEqual(len(row["transactions"]), 2)

    def test_markerless_counterparties_merge_when_prefix_and_suffix_allowed(self):
        groups = {
            "ALPHA BETA SHARE CAP": {
                "counterparty_name": "ALPHA BETA SHARE CAP",
                "total_credits": 10.0,
                "total_debits": 0.0,
                "transaction_count": 1,
            },
            "ALPHA BETA PAYMENT": {
                "counterparty_name": "ALPHA BETA PAYMENT",
                "total_credits": 0.0,
                "total_debits": 5.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(len(merged), 1)
        row = next(iter(merged.values()))
        self.assertEqual(row["total_credits"], 10.0)
        self.assertEqual(row["total_debits"], 5.0)
        self.assertEqual(row["transaction_count"], 2)
        self.assertEqual(row["counterparty_name"], "ALPHA BETA")

    def test_markerless_counterparties_do_not_merge_on_reordered_tokens(self):
        groups = {
            "ALPHA BETA TRADING": {
                "counterparty_name": "ALPHA BETA TRADING",
                "total_credits": 10.0,
                "total_debits": 0.0,
                "transaction_count": 1,
            },
            "BETA ALPHA SERVICES": {
                "counterparty_name": "BETA ALPHA SERVICES",
                "total_credits": 0.0,
                "total_debits": 5.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(len(merged), 2)

    def test_muhafiz_technology_variants_merge_to_sdn_bhd(self):
        groups = {
            "MUHAFIZ TECHNOLOGY MTSB": {
                "counterparty_name": "MUHAFIZ TECHNOLOGY MTSB",
                "total_credits": 0.0,
                "total_debits": 700000.0,
                "transaction_count": 1,
            },
            "MUHAFIZ TECHNOLOGY SHAHARUDDIN B SAMSI": {
                "counterparty_name": "MUHAFIZ TECHNOLOGY SHAHARUDDIN B SAMSI",
                "total_credits": 0.0,
                "total_debits": 430000.0,
                "transaction_count": 2,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(set(merged.keys()), {"MUHAFIZ TECHNOLOGY SDN BHD"})
        row = merged["MUHAFIZ TECHNOLOGY SDN BHD"]
        self.assertEqual(row["total_debits"], 1130000.0)
        self.assertEqual(row["transaction_count"], 3)

    def test_short_name_merges_into_sdn_bhd_canonical(self):
        groups = {
            "MUHAFIZ PRIMA SDN BHD": {
                "counterparty_name": "MUHAFIZ PRIMA SDN BHD",
                "total_credits": 39600.0,
                "total_debits": 0.0,
                "transaction_count": 2,
            },
            "MUHAFIZ PRIMA": {
                "counterparty_name": "MUHAFIZ PRIMA",
                "total_credits": 0.0,
                "total_debits": 900000.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(set(merged.keys()), {"MUHAFIZ PRIMA SDN BHD"})
        row = merged["MUHAFIZ PRIMA SDN BHD"]
        self.assertEqual(row["total_credits"], 39600.0)
        self.assertEqual(row["total_debits"], 900000.0)
        self.assertEqual(row["transaction_count"], 3)

    def test_human_names_with_bin_variants_merge_when_tail_is_similar(self):
        groups = {
            "MOHD AMIN BIN ABDULLAH": {
                "counterparty_name": "MOHD AMIN BIN ABDULLAH",
                "total_credits": 10.0,
                "total_debits": 0.0,
                "transaction_count": 1,
            },
            "MOHD AMIN B ABDULAH": {
                "counterparty_name": "MOHD AMIN B ABDULAH",
                "total_credits": 0.0,
                "total_debits": 5.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(len(merged), 1)
        row = next(iter(merged.values()))
        self.assertEqual(row["total_credits"], 10.0)
        self.assertEqual(row["total_debits"], 5.0)
        self.assertEqual(row["transaction_count"], 2)

    def test_shaharuddin_sams_and_samsi_credit_card_merge_to_longer_name(self):
        groups = {
            "SHAHARUDDIN SAMS": {
                "counterparty_name": "SHAHARUDDIN SAMS",
                "total_credits": 0.0,
                "total_debits": 334537.28,
                "transaction_count": 26,
            },
            "SHAHARUDDIN SAMSI CREDIT CARD": {
                "counterparty_name": "SHAHARUDDIN SAMSI CREDIT CARD",
                "total_credits": 0.0,
                "total_debits": 378200.13,
                "transaction_count": 12,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(set(merged.keys()), {"SHAHARUDDIN SAMSI"})
        row = merged["SHAHARUDDIN SAMSI"]
        self.assertEqual(row["total_debits"], 712737.41)
        self.assertEqual(row["transaction_count"], 38)

    def test_shaharuddin_abb_prefers_bin_variant(self):
        groups = {
            "SHAHARUDDIN ABB": {
                "counterparty_name": "SHAHARUDDIN ABB",
                "total_credits": 0.0,
                "total_debits": 22910.0,
                "transaction_count": 1,
            },
            "SHAHARUDDIN BIN ABB": {
                "counterparty_name": "SHAHARUDDIN BIN ABB",
                "total_credits": 0.0,
                "total_debits": 33000.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(set(merged.keys()), {"SHAHARUDDIN BIN ABB"})
        row = merged["SHAHARUDDIN BIN ABB"]
        self.assertEqual(row["total_debits"], 55910.0)
        self.assertEqual(row["transaction_count"], 2)

    def test_noraziyan_oth_noise_variants_merge(self):
        names = [
            "ANNUAL DINNER NORAZIYAN OTH",
            "GOLF NORAZIYAN OTH KC ZUL SPORT SHOP",
            "MUHD FAHMI NORAZIYAN OTHM",
            "NORAZIYAN OTH HP SETTLEMENT",
            "NORAZIYAN OTH PRESTRO",
            "PERUNTUKAN NORAZIYAN OTHM",
        ]
        self.assertEqual([clean_counterparty_name(name) for name in names], ["NORAZIYAN OTH"] * 6)

        groups = {
            name: {
                "counterparty_name": name,
                "total_credits": 0.0,
                "total_debits": 1.0,
                "transaction_count": 1,
            }
            for name in names
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(set(merged.keys()), {"NORAZIYAN OTH"})
        row = merged["NORAZIYAN OTH"]
        self.assertEqual(row["total_debits"], 6.0)
        self.assertEqual(row["transaction_count"], 6)

    def test_shared_three_token_prefix_ignores_allowed_suffixes(self):
        groups = {
            "ALPHA BETA GAMMA CREDIT CARD": {
                "counterparty_name": "ALPHA BETA GAMMA CREDIT CARD",
                "total_credits": 10.0,
                "total_debits": 0.0,
                "transaction_count": 1,
            },
            "ALPHA BETA GAMMA CASH": {
                "counterparty_name": "ALPHA BETA GAMMA CASH",
                "total_credits": 0.0,
                "total_debits": 5.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(set(merged.keys()), {"ALPHA BETA GAMMA"})
        row = merged["ALPHA BETA GAMMA"]
        self.assertEqual(row["total_credits"], 10.0)
        self.assertEqual(row["total_debits"], 5.0)
        self.assertEqual(row["transaction_count"], 2)

    def test_shared_three_token_prefix_prefers_legal_suffix_canonical(self):
        groups = {
            "ALPHA BETA GAMMA SDN BHD": {
                "counterparty_name": "ALPHA BETA GAMMA SDN BHD",
                "total_credits": 10.0,
                "total_debits": 0.0,
                "transaction_count": 1,
            },
            "ALPHA BETA GAMMA PAYMENT": {
                "counterparty_name": "ALPHA BETA GAMMA PAYMENT",
                "total_credits": 0.0,
                "total_debits": 5.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(set(merged.keys()), {"ALPHA BETA GAMMA SDN BHD"})
        row = merged["ALPHA BETA GAMMA SDN BHD"]
        self.assertEqual(row["total_credits"], 10.0)
        self.assertEqual(row["total_debits"], 5.0)

    def test_shared_three_token_prefix_requires_allowed_suffixes(self):
        groups = {
            "ALPHA BETA GAMMA PROJECT": {
                "counterparty_name": "ALPHA BETA GAMMA PROJECT",
                "total_credits": 10.0,
                "total_debits": 0.0,
                "transaction_count": 1,
            },
            "ALPHA BETA GAMMA VENDOR": {
                "counterparty_name": "ALPHA BETA GAMMA VENDOR",
                "total_credits": 0.0,
                "total_debits": 5.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(len(merged), 2)

    def test_shared_three_token_prefix_skips_protected_buckets(self):
        groups = {
            "TRANSFER FEE": {
                "counterparty_name": "TRANSFER FEE",
                "total_credits": 10.0,
                "total_debits": 0.0,
                "transaction_count": 1,
            },
            "TRANSFER FEE CREDIT CARD": {
                "counterparty_name": "TRANSFER FEE CREDIT CARD",
                "total_credits": 0.0,
                "total_debits": 5.0,
                "transaction_count": 1,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(len(merged), 2)

    def test_build_transactions_by_party_applies_iterative_merge(self):
        import pandas as pd

        rows = pd.DataFrame(
            [
                {
                    "date": "2026-01-01",
                    "description": "TR IBG FATHIN SYAIRAH NAJL",
                    "party_name": "FATHIN SYAIRAH NAJL",
                    "credit": 0.0,
                    "debit": 100.0,
                    "source_file": "sample.pdf",
                },
                {
                    "date": "2026-01-02",
                    "description": "TR IBG FATHIN SYAIRAH NAJLA",
                    "party_name": "FATHIN SYAIRAH NAJLA",
                    "credit": 25.0,
                    "debit": 0.0,
                    "source_file": "sample.pdf",
                },
            ]
        )

        party_tables = build_transactions_by_party(rows)
        self.assertEqual(len(party_tables), 1)
        self.assertEqual(party_tables[0]["party"], "FATHIN SYAIRAH NAJLA")
        self.assertEqual(party_tables[0]["count"], 2)
        self.assertEqual(party_tables[0]["total_credit"], 25.0)
        self.assertEqual(party_tables[0]["total_debit"], 100.0)

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

    def test_own_related_list_groups_related_rows_by_confirmed_names(self):
        classified = [
            {
                "date": "2026-01-01",
                "description": "TR IBG DAYANG SITI RAUDZAH OFFICE ELECTRICITY",
                "credit": 0.0,
                "debit": 3571.65,
                "classification": {"primary": "C04", "side": "DR"},
            },
            {
                "date": "2026-01-02",
                "description": "TR IBG SHAHARUDDIN SAMSI CC BOS SHAH",
                "credit": 0.0,
                "debit": 18809.31,
                "classification": {"primary": "C04", "side": "DR"},
            },
            {
                "date": "2026-01-03",
                "description": "TR IBG CLOSE",
                "credit": 1052.07,
                "debit": 0.0,
                "classification": {"primary": "C01", "side": "CR"},
            },
        ]
        counterparty_lookup = {
            0: "DAYANG SITI RAUDZAH OFFICE ELECTRICITY",
            1: "SHAHARUDDIN SAMSI CC BOS SHAH",
            2: "CLOSE",
        }

        rows = _build_own_related_transactions_list_track2(
            classified,
            counterparty_lookup=counterparty_lookup,
            related_parties=["DAYANG SITI RAUDZAH", "SHAHARUDDIN SAMS"],
        )

        self.assertEqual(rows[0]["party_name"], "DAYANG SITI RAUDZAH")
        self.assertEqual(rows[1]["party_name"], "SHAHARUDDIN SAMS")
        self.assertEqual(rows[2]["party_name"], "CLOSE")


if __name__ == "__main__":
    unittest.main()
