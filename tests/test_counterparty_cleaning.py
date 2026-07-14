import unittest

from cimb import annotate_cimb_counterparties, extract_cimb_party_name
from maybank import annotate_maybank_counterparties
from app import (
    _align_related_party_candidates_to_counterparty_rows,
    build_own_related_party_groups_for_report,
    build_track2_counterparty_ledger,
    _report_related_party_entries,
    _top_parties_from_counterparty_rows,
    build_report_counterparty_ledger_rows,
    filter_report_related_parties,
    generate_interactive_html,
    generate_excel_report,
    get_report_counterparty_rows_from_data,
    partition_related_party_candidates_for_manager,
    prepare_top_parties_for_report,
)
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
        self.assertEqual(clean_counterparty_name("ALPHA MTSB"), "ALPHA")
        self.assertEqual(clean_counterparty_name("ALPHA SDN BH"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA SDN BHD"), "ALPHA SDN BHD")
        self.assertEqual(clean_counterparty_name("ALPHA BERHAD"), "ALPHA BHD")
        self.assertEqual(clean_counterparty_name("MUHAFIZ PRIMA SDN. B"), "MUHAFIZ PRIMA SDN BHD")

    def test_does_not_append_company_suffix_without_marker(self):
        self.assertEqual(clean_counterparty_name("MUHAFIZ SECURITY"), "MUHAFIZ SECURITY")

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
            clean_counterparty_name("BATAM INDONESIA SHAHARUDDIN SAM PESONA GOLF"),
            "SHAHARUDDIN SAM",
        )
        self.assertEqual(
            clean_counterparty_name("GOLF TOURS SHAHARUDDIN SAM ADDITIONAL PACKAGE"),
            "SHAHARUDDIN SAM",
        )
        self.assertEqual(clean_counterparty_name("SHAHARUDDIN SAM KUCHING"), "SHAHARUDDIN SAM")
        self.assertEqual(
            clean_counterparty_name("KETUA UNIT KESELAMAT DAYANG SURIATI BINT FAREWELL"),
            "DAYANG SURIATI",
        )

    def test_expands_sdn_from_transfer_description(self):
        self.assertEqual(
            clean_counterparty_name("TR IBG MUHAFIZ SECURITY SDN TRANSFER BACK TO MBB"),
            "MUHAFIZ SECURITY SDN BHD",
        )
        self.assertEqual(
            normalise_counterparty_for_ledger("MUHAFIZ SECURITY SDN."),
            "MUHAFIZ SECURITY SDN BHD",
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

    def test_single_clean_bucket_does_not_revert_to_noisy_raw_alias(self):
        groups = {
            "DAYANG SITI RAUDZAH": {
                "counterparty_name": "DAYANG SITI RAUDZAH",
                "raw_names": {"DAYANG SITI RAUDZAH OFFICE ELECTRICITY"},
                "total_credits": 0.0,
                "total_debits": 179632.30,
                "credit_count": 0,
                "debit_count": 33,
                "transaction_count": 33,
                "transactions": [{"description": "TR IBG DAYANG SITI RAUDZAH OFFICE ELECTRICITY"}],
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(list(merged.keys()), ["DAYANG SITI RAUDZAH"])
        self.assertEqual(merged["DAYANG SITI RAUDZAH"]["transaction_count"], 33)

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

    def test_muhafiz_technology_variants_merge_without_sdn_bhd(self):
        self.assertEqual(
            clean_counterparty_name("MUHAFIZ TECHNOLOGY MTSB SHARE CAP"),
            "MUHAFIZ TECHNOLOGY",
        )
        self.assertEqual(
            clean_counterparty_name("MUHAFIZ TECHNOLOGY PAYMENT"),
            "MUHAFIZ TECHNOLOGY",
        )

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
        self.assertEqual(set(merged.keys()), {"MUHAFIZ TECHNOLOGY"})
        row = merged["MUHAFIZ TECHNOLOGY"]
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

    def test_shaharuddin_sam_wrapped_descriptions_merge(self):
        groups = {
            "BATAM INDONESIA SHAHARUDDIN SAM PESONA GOLF": {
                "counterparty_name": "BATAM INDONESIA SHAHARUDDIN SAM PESONA GOLF",
                "total_credits": 0.0,
                "total_debits": 1800.0,
                "transaction_count": 1,
            },
            "GOLF TOURS SHAHARUDDIN SAM ADDITIONAL PACKAGE": {
                "counterparty_name": "GOLF TOURS SHAHARUDDIN SAM ADDITIONAL PACKAGE",
                "total_credits": 0.0,
                "total_debits": 4200.0,
                "transaction_count": 1,
            },
            "SHAHARUDDIN SAM KUCHING": {
                "counterparty_name": "SHAHARUDDIN SAM KUCHING",
                "total_credits": 0.0,
                "total_debits": 729647.41,
                "transaction_count": 37,
            },
        }

        merged = _merge_counterparty_groups(groups)
        self.assertEqual(set(merged.keys()), {"SHAHARUDDIN SAM"})
        row = merged["SHAHARUDDIN SAM"]
        self.assertEqual(row["total_debits"], 735647.41)
        self.assertEqual(row["transaction_count"], 39)

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

    def test_maybank_rows_keep_counterparty_fields_for_ledger(self):
        rows = [
            {
                "description": "PAYMENT FR A/C EPF DPE * 000000123456",
                "credit": 1200.0,
                "debit": 0.0,
            },
            {
                "description": "PAYMENT FR A/C EPF DPE * 000000654321",
                "credit": 800.0,
                "debit": 0.0,
            },
        ]

        annotate_maybank_counterparties(rows)

        self.assertEqual(rows[0]["counterparty_name_raw"], "EPF DPE")
        self.assertEqual(rows[0]["counterparty_name_clean"], "EPF DPE")
        self.assertEqual(rows[1]["counterparty_name_clean"], "EPF DPE")
        self.assertEqual(rows[0]["party_name"], "EPF DPE")

    def test_track2_ledger_uses_maybank_parser_counterparty_fields(self):
        rows = [
            {
                "date": "2026-01-01",
                "description": "PAYMENT FR A/C EPF DPE * 000000123456",
                "credit": 1200.0,
                "debit": 0.0,
                "balance": 1200.0,
                "bank": "Maybank",
            }
        ]

        annotate_maybank_counterparties(rows)
        ledger = build_track2_counterparty_ledger(rows)

        self.assertEqual(ledger["counterparties"][0]["counterparty_name"], "EPF DPE")
        self.assertEqual(ledger["counterparties"][0]["transaction_count"], 1)
        self.assertEqual(ledger["extraction_stats"]["pattern_matched"], 1)
        self.assertEqual(ledger["extraction_stats"]["raw_fallback"], 0)

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

    def test_report_top_parties_use_visible_ledger_rows_and_drop_parser_buckets(self):
        cp_ledger = {
            "counterparties": [
                {
                    "counterparty_name": "MTH END",
                    "transaction_count": 1,
                    "credit_count": 1,
                    "debit_count": 0,
                    "total_credits": 999999.0,
                    "total_debits": 0.0,
                    "transactions": [],
                },
                {
                    "counterparty_name": "UNKNOWN",
                    "transaction_count": 1,
                    "credit_count": 1,
                    "debit_count": 0,
                    "total_credits": 888888.0,
                    "total_debits": 0.0,
                    "transactions": [],
                },
                {
                    "counterparty_name": "ALPHA CUSTOMER",
                    "transaction_count": 1,
                    "credit_count": 1,
                    "debit_count": 0,
                    "total_credits": 1000.0,
                    "total_debits": 0.0,
                    "transactions": [],
                },
                {
                    "counterparty_name": "SHAHARUDDIN BIN SAM",
                    "transaction_count": 1,
                    "credit_count": 0,
                    "debit_count": 1,
                    "total_credits": 0.0,
                    "total_debits": 500.0,
                    "transactions": [],
                },
            ]
        }

        ledger_rows = build_report_counterparty_ledger_rows(
            cp_ledger,
            related_parties=[{"name": "SHAHARUDDIN SAMS", "relationship": "Affiliate"}],
        )
        top_parties = _top_parties_from_counterparty_rows(ledger_rows, limit=None)
        party_view = prepare_top_parties_for_report(top_parties, limit=10)

        self.assertEqual([p["party_name"] for p in party_view["payers"]], ["ALPHA CUSTOMER"])
        self.assertEqual([p["party_name"] for p in party_view["payees"]], ["SHAHARUDDIN SAMS"])
        self.assertTrue(party_view["payees"][0]["is_related_party"])

    def test_html_counterparty_summary_cards_use_unknown_as_raw_fallback(self):
        counterparty_rows = [
            {
                "counterparty_name": "ALPHA CUSTOMER",
                "transaction_count": 2,
                "credit_count": 2,
                "debit_count": 0,
                "total_credits": 2000.0,
                "total_debits": 0.0,
                "pattern_matched": 2,
                "special_bucket": 0,
                "raw_fallback": 0,
                "transactions": [],
            },
            {
                "counterparty_name": "UNKNOWN",
                "transaction_count": 3,
                "credit_count": 1,
                "debit_count": 2,
                "total_credits": 100.0,
                "total_debits": 250.0,
                "pattern_matched": 0,
                "special_bucket": 3,
                "raw_fallback": 0,
                "transactions": [],
            },
            {
                "counterparty_name": "BANK FEES",
                "transaction_count": 1,
                "credit_count": 0,
                "debit_count": 1,
                "total_credits": 0.0,
                "total_debits": 10.0,
                "pattern_matched": 0,
                "special_bucket": 1,
                "raw_fallback": 0,
                "transactions": [],
            },
        ]

        html = generate_interactive_html({
            "report_info": {
                "company_name": "ACME SDN BHD",
                "schema_version": "6.3.5",
            },
            "accounts": [],
            "monthly_analysis": [],
            "consolidated": {},
            "counterparty_ledger": {
                "counterparties": counterparty_rows,
                "total_counterparties": len(counterparty_rows),
                "extraction_stats": {
                    "pattern_matched": 2,
                    "special_bucket": 4,
                    "raw_fallback": 0,
                },
            },
            "report_counterparty_rows": counterparty_rows,
        })

        self.assertIn('<div class="lbl">Total Counterparties</div>', html)
        self.assertIn('<div class="summary-card"><div class="val">2</div><div class="lbl">Pattern matched</div></div>', html)
        self.assertIn('<div class="summary-card"><div class="val">1</div><div class="lbl">Special bucket</div></div>', html)
        self.assertIn('<div class="summary-card"><div class="val">3</div><div class="lbl">Raw fallback</div></div>', html)
        self.assertNotIn("Original (pre-clean)", html)
        self.assertNotIn("Merges Performed", html)
        self.assertNotIn("Purpose Strips", html)
        self.assertNotIn("Merged from banks", html)

    def test_track2_ledger_counts_unknown_counterparty_as_raw_fallback(self):
        ledger = build_track2_counterparty_ledger([
            {
                "date": "2026-01-01",
                "description": "NO COUNTERPARTY FOUND",
                "credit": 100.0,
                "debit": 0.0,
                "balance": 500.0,
            }
        ])

        stats = ledger["extraction_stats"]
        self.assertEqual(stats["raw_fallback"], 1)
        self.assertEqual(stats["special_bucket"], 0)
        self.assertEqual(ledger["counterparties"][0]["counterparty_name"], "UNKNOWN")
        self.assertEqual(ledger["counterparties"][0]["raw_fallback"], 1)

    def test_special_buckets_are_not_report_related_parties(self):
        self.assertEqual(
            _report_related_party_entries([
                {"name": "TRANSFER FEE", "relationship": "Affiliate"},
                {"name": "UNKNOWN", "relationship": "Affiliate"},
                {"name": "SPECIAL_BUCKET", "relationship": "Affiliate"},
                {"name": "SPECIAL BUCKET", "relationship": "Affiliate"},
                {"name": "SHAHARUDDIN SAMS", "relationship": "Affiliate"},
            ]),
            [("SHAHARUDDIN SAMS", "Affiliate")],
        )
        self.assertEqual(
            filter_report_related_parties([
                {"name": "SPECIAL_BUCKET", "relationship": "Affiliate"},
                {"name": "SHAHARUDDIN SAMS", "relationship": "Affiliate"},
            ]),
            [{"name": "SHAHARUDDIN SAMS", "relationship": "Affiliate"}],
        )

    def test_related_party_manager_splits_high_from_possible_candidates(self):
        known, possible = partition_related_party_candidates_for_manager([
            {"name": "HIGH PARTY", "confidence": "HIGH"},
            {"name": "MEDIUM PARTY", "confidence": "MEDIUM"},
            {"name": "LOW PARTY", "status": "LOW"},
            {"name": "UNKNOWN STATUS", "confidence": "REVIEW"},
        ])

        self.assertEqual([candidate["name"] for candidate in known], ["HIGH PARTY"])
        self.assertEqual(
            [candidate["name"] for candidate in possible],
            ["MEDIUM PARTY", "LOW PARTY"],
        )

    def test_related_party_manager_uses_counterparty_ledger_display_name(self):
        candidates = [
            {
                "name": "MARIANA BINTI AHMAT",
                "confidence": "MEDIUM",
                "total_cr": 0.0,
                "total_dr": 8760.0,
                "signals": ["personal_keyword_sweep"],
            }
        ]
        counterparty_rows = [
            {
                "counterparty_name": "MARIANA AHMAT",
                "total_credits": 0.0,
                "total_debits": 8760.0,
                "transaction_count": 7,
                "transactions": [
                    {
                        "description": "TR TO MARIANA BINTI AHMAT DIRECTOR FEE",
                        "counterparty_name_raw": "MARIANA BINTI AHMAT",
                    }
                ],
            }
        ]

        aligned = _align_related_party_candidates_to_counterparty_rows(candidates, counterparty_rows)

        self.assertEqual(aligned[0]["name"], "MARIANA AHMAT")
        self.assertEqual(aligned[0]["original_name"], "MARIANA BINTI AHMAT")

    def test_own_related_groups_use_matching_counterparty_ledger_transactions(self):
        own_related = {
            "transactions": [
                {
                    "date": "2025-09-01",
                    "description": "TR IBG SHAHARUDDIN SAMS ONE",
                    "amount": 100.0,
                    "type": "DEBIT",
                    "party_type": "RELATED",
                    "party_name": "SHAHARUDDIN SAMS",
                }
            ]
        }
        cp_rows = [
            {
                "counterparty_name": "SHAHARUDDIN SAMS",
                "total_credits": 0.0,
                "total_debits": 300.0,
                "credit_count": 0,
                "debit_count": 2,
                "transaction_count": 2,
                "transactions": [
                    {
                        "date": "2025-09-01",
                        "description": "TR IBG SHAHARUDDIN SAMS ONE",
                        "amount": 100.0,
                        "type": "DEBIT",
                        "balance": 900.0,
                    },
                    {
                        "date": "2025-09-02",
                        "description": "TR IBG SHAHARUDDIN SAMS TWO",
                        "amount": 200.0,
                        "type": "DEBIT",
                        "balance": 700.0,
                    },
                ],
            }
        ]

        groups = build_own_related_party_groups_for_report(
            own_related,
            related_parties=[{"name": "SHAHARUDDIN SAMS", "relationship": "Affiliate"}],
            counterparty_rows=cp_rows,
        )

        shah_group = next(group for group in groups if group["party_name"] == "SHAHARUDDIN SAMS")
        self.assertEqual(shah_group["debit_count"], 2)
        self.assertEqual(shah_group["debits"], 300.0)
        self.assertEqual(len(shah_group["transactions"]), 2)

    def test_own_related_inheritance_ignores_mentions_inside_other_counterparties(self):
        own_related = {
            "transactions": [
                {
                    "date": "2025-09-01",
                    "description": "TR IBG SHAHARUDDIN SAMS ONE",
                    "amount": 100.0,
                    "type": "DEBIT",
                    "party_type": "RELATED",
                    "party_name": "SHAHARUDDIN SAMS",
                }
            ]
        }
        shah_transactions = [
            {
                "date": f"2025-09-{(idx % 28) + 1:02d}",
                "description": f"TR IBG SHAHARUDDIN SAMS {idx + 1}",
                "amount": 735_609.41 if idx == 0 else 1.0,
                "type": "DEBIT",
                "balance": 700.0 - idx,
            }
            for idx in range(39)
        ]
        cp_rows = [
            {
                "counterparty_name": "MUHAFIZ TECHNOLOGY",
                "raw_names": ["MUHAFIZ TECHNOLOGY SHAHARUDDIN B SAMSI"],
                "total_credits": 0.0,
                "total_debits": 1_000_000.0,
                "credit_count": 0,
                "debit_count": 3,
                "transaction_count": 3,
                "transactions": [
                    {
                        "date": "2025-09-03",
                        "description": "ALPHA TRADING PAYMENT FOR SHAHARUDDIN SAMS",
                        "amount": 1_000_000.0,
                        "type": "DEBIT",
                        "party_name": "SHAHARUDDIN SAMS",
                        "counterparty_name_raw": "SHAHARUDDIN SAMS",
                    }
                ],
            },
            {
                "counterparty_name": "SHAHARUDDIN SAMS",
                "total_credits": 0.0,
                "total_debits": 735_647.41,
                "credit_count": 0,
                "debit_count": 39,
                "transaction_count": 39,
                "transactions": shah_transactions,
            },
        ]

        groups = build_own_related_party_groups_for_report(
            own_related,
            related_parties=[{"name": "SHAHARUDDIN SAMS", "relationship": "Affiliate"}],
            counterparty_rows=cp_rows,
        )

        shah_group = next(group for group in groups if group["party_name"] == "SHAHARUDDIN SAMS")
        self.assertEqual(shah_group["credit_count"], 0)
        self.assertEqual(shah_group["debit_count"], 39)
        self.assertEqual(shah_group["transaction_count"], 39)
        self.assertEqual(shah_group["credits"], 0.0)
        self.assertEqual(shah_group["debits"], 735_647.41)
        self.assertEqual(len(shah_group["transactions"]), 39)

    def test_own_related_empty_related_placeholder_inherits_counterparty_ledger(self):
        cp_rows = [
            {
                "counterparty_name": "DAYANG SITI RAUDZAH",
                "total_credits": 50.0,
                "total_debits": 125.0,
                "credit_count": 1,
                "debit_count": 2,
                "transaction_count": 3,
                "transactions": [
                    {
                        "date": "2025-09-01",
                        "description": "IBG DAYANG SITI RAUDZAH REFUND",
                        "amount": 50.0,
                        "type": "CREDIT",
                        "balance": 1050.0,
                    },
                    {
                        "date": "2025-09-02",
                        "description": "IBG DAYANG SITI RAUDZAH PAYMENT",
                        "amount": 75.0,
                        "type": "DEBIT",
                        "balance": 975.0,
                    },
                    {
                        "date": "2025-09-03",
                        "description": "IBG DAYANG SITI RAUDZAH PAYMENT",
                        "amount": 50.0,
                        "type": "DEBIT",
                        "balance": 925.0,
                    },
                ],
            }
        ]

        groups = build_own_related_party_groups_for_report(
            {"transactions": []},
            related_parties=[{"name": "DAYANG SITI RAUDZAH", "relationship": "Affiliate"}],
            counterparty_rows=cp_rows,
        )

        dayang_group = next(group for group in groups if group["party_name"] == "DAYANG SITI RAUDZAH")
        self.assertEqual(dayang_group["credit_count"], 1)
        self.assertEqual(dayang_group["debit_count"], 2)
        self.assertEqual(dayang_group["credits"], 50.0)
        self.assertEqual(dayang_group["debits"], 125.0)
        self.assertEqual(len(dayang_group["transactions"]), 3)

    def test_own_related_own_party_inherits_counterparty_display_name(self):
        cp_rows = [
            {
                "counterparty_name": "MUHAFIZ SECURITY SDN BHD",
                "total_credits": 2_784_136.22,
                "total_debits": 872_136.0,
                "credit_count": 14,
                "debit_count": 56,
                "transaction_count": 70,
                "transactions": [
                    {
                        "date": "2025-09-04",
                        "description": "IBG CREDIT MTH END MUHAFIZ SECURITY SDN",
                        "amount": 500_000.0,
                        "type": "CREDIT",
                        "balance": 1_770_529.95,
                    },
                    {
                        "date": "2025-09-04",
                        "description": "TR IBG MUHAFIZ SECURITY SDN TRANSFER BACK TO MBB",
                        "amount": 400_000.0,
                        "type": "DEBIT",
                        "balance": 1_875_782.28,
                    },
                ],
            }
        ]

        groups = build_own_related_party_groups_for_report(
            {"transactions": []},
            company_name="MUHAFIZ SECURITY SDN. BHD.",
            counterparty_rows=cp_rows,
        )

        own_group = next(group for group in groups if group["badge_type"] == "OP")
        self.assertEqual(own_group["party_name"], "MUHAFIZ SECURITY SDN BHD")
        self.assertEqual(own_group["credit_count"], 14)
        self.assertEqual(own_group["debit_count"], 56)
        self.assertEqual(own_group["transaction_count"], 70)
        self.assertEqual(own_group["credits"], 2_784_136.22)
        self.assertEqual(own_group["debits"], 872_136.0)
        self.assertEqual(len(own_group["transactions"]), 2)

    def test_excel_counterparty_own_party_count_matches_cp_ledger(self):
        import openpyxl

        cp_transactions = [
            {
                "date": f"2025-09-{(idx % 28) + 1:02d}",
                "description": f"OWN PARTY TRANSFER {idx + 1}",
                "amount": 100.0,
                "type": "DEBIT",
                "balance": 1000.0 - idx,
            }
            for idx in range(70)
        ]
        cp_row = {
            "counterparty_name": "MUHAFIZ SECURITY SDN BHD",
            "total_credits": 1400.0,
            "total_debits": 5600.0,
            "credit_count": 14,
            "debit_count": 56,
            "transaction_count": 70,
            "transactions": cp_transactions,
        }
        own_related_rows = [
            {
                "date": f"2025-09-{(idx % 28) + 1:02d}",
                "description": f"RAW OWN PARTY TRANSFER {idx + 1}",
                "amount": 100.0,
                "type": "DEBIT",
                "party_type": "OWN",
                "party_name": "MUHAFIZ SECURITY",
            }
            for idx in range(60)
        ]
        workbook_bytes = generate_excel_report(
            {
                "report_info": {
                    "company_name": "MUHAFIZ SECURITY SDN. BHD.",
                    "related_parties": [],
                },
                "own_related_transactions": {"transactions": own_related_rows, "summary": {}},
                "counterparty_ledger": {"counterparties": [cp_row]},
                "report_counterparty_rows": [cp_row],
                "transactions": [],
                "consolidated": {},
                "monthly_analysis": [],
                "accounts": [],
            }
        )

        wb = openpyxl.load_workbook(workbook_bytes, data_only=True)
        ws = wb["Counterparty"]

        self.assertEqual(ws.cell(row=5, column=6).value, 70)

    def test_reports_prefer_streamlit_counterparty_rows_over_raw_ledger(self):
        cp_ledger = {
            "counterparties": [
                {
                    "counterparty_name": "MUHAFIZ PRIMA SDN BHD",
                    "transaction_count": 3,
                    "credit_count": 2,
                    "debit_count": 1,
                    "total_credits": 39600.0,
                    "total_debits": 900000.0,
                    "transactions": [],
                },
                {
                    "counterparty_name": "MUHAFIZ SECURITY SDN.",
                    "transaction_count": 70,
                    "credit_count": 14,
                    "debit_count": 56,
                    "total_credits": 2784136.22,
                    "total_debits": 872136.0,
                    "transactions": [],
                },
                {
                    "counterparty_name": "MUHAFIZ TECHNOLOGY",
                    "transaction_count": 3,
                    "credit_count": 0,
                    "debit_count": 3,
                    "total_credits": 0.0,
                    "total_debits": 1130000.0,
                    "transactions": [],
                },
            ]
        }
        ui_rows = [
            {
                "counterparty_name": "MUHAFIZ PRIMA SDN BHD",
                "transaction_count": 3,
                "credit_count": 2,
                "debit_count": 1,
                "total_credits": 39600.0,
                "total_debits": 900000.0,
                "transactions": [],
            },
            {
                "counterparty_name": "MUHAFIZ SECURITY SDN. BHD.",
                "transaction_count": 70,
                "credit_count": 14,
                "debit_count": 56,
                "total_credits": 2784136.22,
                "total_debits": 872136.0,
                "transactions": [],
            },
        ]

        rows = get_report_counterparty_rows_from_data(
            {"counterparty_ledger": cp_ledger, "report_counterparty_rows": ui_rows},
            cp_ledger,
        )

        self.assertEqual(
            [row["counterparty_name"] for row in rows],
            ["MUHAFIZ PRIMA SDN BHD", "MUHAFIZ SECURITY SDN BHD"],
        )
        self.assertNotIn("MUHAFIZ TECHNOLOGY", {row["counterparty_name"] for row in rows})
        self.assertEqual(rows[1]["total_credits"], 2784136.22)
        self.assertEqual(rows[1]["total_debits"], 872136.0)


if __name__ == "__main__":
    unittest.main()
