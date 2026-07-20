import unittest

from cimb import annotate_cimb_counterparties, extract_cimb_party_name
from alliance import annotate_alliance_counterparties, extract_alliance_party_name
from agro_bank import extract_agrobank_party_name
from maybank import annotate_maybank_counterparties, extract_maybank_party_name
from ambank import clean_ambank_company_name, extract_ambank_company_name
from pdf_utils import _clean_candidate_name, extract_company_name
from app import (
    _align_related_party_candidates_to_counterparty_rows,
    build_own_related_party_groups_for_report,
    build_track2_counterparty_ledger,
    calculate_monthly_summary,
    _report_related_party_entries,
    _top_parties_from_counterparty_rows,
    build_report_counterparty_ledger_rows,
    detect_related_party_candidates,
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

    def test_account_marker_slash_does_not_become_counterparty_initials(self):
        self.assertEqual(
            clean_counterparty_name("A/C CAC ENGINEERING SDN BHD"),
            "CAC ENGINEERING SDN BHD",
        )
        self.assertEqual(
            clean_counterparty_name("may25 a/c MUN HEAN (MALAYSIA) may25 a/c MUN HEAN (MALAYSIA) SDN BERHAD"),
            "MUN HEAN MALAYSIA MUN HEAN MALAYSIA SDN BHD",
        )
        self.assertEqual(
            clean_counterparty_name("A/C AC EVERCOM ENGINEERING"),
            "AC EVERCOM ENGINEERING",
        )
        self.assertEqual(
            clean_counterparty_name("AC EVERCOM ENGINEERING"),
            "AC EVERCOM ENGINEERING",
        )

    def test_statement_company_name_truncates_after_sdn_bhd(self):
        self.assertEqual(
            _clean_candidate_name("DMC TRAVEL AND TOURS SDN. BHD. 結單日期 : 31/08/25"),
            "DMC TRAVEL AND TOURS SDN BHD",
        )
        self.assertEqual(
            _clean_candidate_name("DMC TRAVEL AND TOURS SDN BHD 結單日期: 31/08/25"),
            "DMC TRAVEL AND TOURS SDN BHD",
        )

    def test_maybank_header_extracts_suffixless_agency_name(self):
        class FakePage:
            def __init__(self, text):
                self._text = text

            def extract_text(self):
                return self._text

        class FakePdf:
            pages = [
                FakePage(
                    "\n".join(
                        [
                            "Maybank Islamic Berhad (787435-M)",
                            "IBS TELUK INTAN",
                            "MUKA/ PAGE : 1",
                            "TARIKH PENYATA",
                            "LSR AGENCY \u7d50\u55ae\u65e5\u671f : 31/03/25",
                            "2121 KM 2 1/2 OPP KASTAM ,JALAN STATEMENT DATE",
                            "NOMBOR AKAUN",
                            "558060518128",
                            "01/03 TRANSFER TO A/C 3,000.00+ 39,752.28",
                            "GK GROUP 88 ENTERPR*",
                        ]
                    )
                )
            ]

        self.assertEqual(extract_company_name(FakePdf(), max_pages=2), "LSR AGENCY")

    def test_ambank_header_extracts_company_after_branch_number(self):
        class FakePage:
            def __init__(self, text):
                self._text = text

            def extract_text(self, **kwargs):
                return self._text

        class FakePdf:
            pages = [
                FakePage(
                    "\n".join(
                        [
                            "AmBank (M) Berhad",
                            "JOHOR BAHRU - MELODIES GARDEN - 044 RE CONCEPT RESOURCES",
                            "Account No 1234567890",
                            "Statement Date / Tarikh Penyata : 31/05/2026",
                        ]
                    )
                )
            ]

        self.assertEqual(
            clean_ambank_company_name("JOHOR BAHRU - MELODIES GARDEN - 044 RE CONCEPT RESOURCES"),
            "RE CONCEPT RESOURCES",
        )
        self.assertEqual(extract_ambank_company_name(FakePdf(), max_pages=2), "RE CONCEPT RESOURCES")

    def test_ambank_header_extracts_company_after_branch_line(self):
        class FakePage:
            def __init__(self, text):
                self._text = text

            def extract_text(self, **kwargs):
                return self._text

        class FakePdf:
            pages = [
                FakePage(
                    "\n".join(
                        [
                            "Dilindungi oleh PIDM setakat RM250,000",
                            "TAMAN MALURI - CHERAS - 142",
                            "PLENTITUDE ENERGY SDN BHD",
                            "A-2-07 ONE SOUTH STREETMALL",
                            "JALAN OS SEKSYEN 6",
                            "TAMAN SERDANG PERDANA",
                            "43300 SERI KEMBANGAN",
                        ]
                    )
                )
            ]

        self.assertEqual(extract_ambank_company_name(FakePdf(), max_pages=2), "PLENTITUDE ENERGY SDN BHD")

    def test_ambank_monthly_summary_reuses_clean_statement_company(self):
        rows = [
            {
                "date": "2024-04-02",
                "description": "INWARD IBG, V2 AUTO SDN BHD",
                "debit": 0,
                "credit": 100,
                "balance": 100,
                "bank": "Ambank",
                "company_name": "02APR INWARD IBG, V2 AUTO SDN BHD",
            },
            {
                "date": "2024-06-03",
                "description": "INWARD IBG",
                "debit": 0,
                "credit": 100,
                "balance": 200,
                "bank": "Ambank",
                "company_name": "JOHOR BAHRU - MELODIES GARDEN - 044 RE CONCEPT RESOURCES",
            },
            {
                "date": "2024-07-02",
                "description": "INWARD IBG, V2 AUTO SDN BHD",
                "debit": 0,
                "credit": 100,
                "balance": 300,
                "bank": "Ambank",
                "company_name": "02JUL INWARD IBG, V2 AUTO SDN BHD",
            },
        ]

        summary = calculate_monthly_summary(rows)

        self.assertEqual(
            [row["company_name"] for row in summary],
            ["RE CONCEPT RESOURCES", "RE CONCEPT RESOURCES", "RE CONCEPT RESOURCES"],
        )

    def test_ambank_monthly_summary_prefers_same_account_header_company(self):
        rows = [
            {
                "date": "2024-03-02",
                "description": "INWARD IBG KINI SDN BHD",
                "debit": 0,
                "credit": 100,
                "balance": 100,
                "bank": "Ambank",
                "account_no": "8881019180298",
                "company_name": "KINI SDN BHD",
            },
            {
                "date": "2024-04-02",
                "description": "INWARD IBG PROCESSING SDN BHD",
                "debit": 0,
                "credit": 100,
                "balance": 200,
                "bank": "Ambank",
                "account_no": "8881019180298",
                "company_name": "PROCESSING SDN BHD",
            },
            {
                "date": "2024-05-02",
                "description": "CREDIT TRANSFER",
                "debit": 0,
                "credit": 100,
                "balance": 300,
                "bank": "Ambank",
                "account_no": "8881019180298",
                "company_name": "PLENTITUDE ENERGY SDN BHD",
            },
        ]

        summary = calculate_monthly_summary(rows)

        self.assertEqual(
            [row["company_name"] for row in summary],
            ["PLENTITUDE ENERGY SDN BHD", "PLENTITUDE ENERGY SDN BHD", "PLENTITUDE ENERGY SDN BHD"],
        )

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

    def test_maybank_ac_counterparty_keeps_dmc_travel_name(self):
        self.assertEqual(
            extract_maybank_party_name("TRANSFER FR A/C DMC TRAVEL AND TOUR* Buraq Hotel mekah"),
            "DMC TRAVEL AND TOUR",
        )

        rows = [
            {
                "date": "2025-08-22",
                "description": "TRANSFER FR A/C DMC TRAVEL AND TOUR* Buraq Hotel mekah",
                "credit": 0.0,
                "debit": 3513.60,
                "balance": 5799.39,
                "bank": "Maybank",
                "company_name": "DMC TRAVEL AND TOURS SDN BHD",
            },
            {
                "date": "2025-12-27",
                "description": "TRANSFER TO A/C DMC TRAVEL AND TOUR* Visa MBB CT",
                "credit": 650.0,
                "debit": 0.0,
                "balance": 1388.43,
                "bank": "Maybank",
                "company_name": "DMC TRAVEL AND TOURS SDN BHD",
            },
        ]

        annotate_maybank_counterparties(rows)
        ledger = build_track2_counterparty_ledger(rows)

        self.assertEqual(ledger["counterparties"][0]["counterparty_name"], "DMC TRAVEL AND TOUR")
        self.assertEqual(ledger["counterparties"][0]["transaction_count"], 2)

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

    def test_alliance_rows_keep_counterparty_fields_for_ledger(self):
        self.assertEqual(
            extract_alliance_party_name("CR ADVICE - IBG REF123 MEGA SUPPLIES SDN BHD"),
            "MEGA SUPPLIES SDN BHD",
        )

        rows = [
            {
                "date": "2026-01-01",
                "description": "CR ADVICE - IBG REF123 MEGA SUPPLIES SDN BHD",
                "description_lines": ["CR ADVICE - IBG REF123 MEGA SUPPLIES SDN BHD"],
                "credit": 1200.0,
                "debit": 0.0,
                "balance": 3200.0,
                "bank": "Alliance Bank",
            },
            {
                "date": "2026-01-02",
                "description": "Instant Transfer 123456 SHAHARUDDIN SAMS",
                "description_lines": ["Instant Transfer 123456 SHAHARUDDIN SAMS"],
                "credit": 0.0,
                "debit": 300.0,
                "balance": 2900.0,
                "bank": "Alliance Bank",
            },
        ]

        annotate_alliance_counterparties(rows)
        ledger = build_track2_counterparty_ledger(rows)

        names = {row["counterparty_name"] for row in ledger["counterparties"]}
        self.assertEqual(rows[0]["counterparty_name_raw"], "MEGA SUPPLIES SDN BHD")
        self.assertEqual(rows[0]["party_name"], "MEGA SUPPLIES SDN BHD")
        self.assertIn("MEGA SUPPLIES SDN BHD", names)
        self.assertIn("SHAHARUDDIN SAMS", names)
        self.assertEqual(ledger["extraction_stats"]["pattern_matched"], 2)
        self.assertEqual(ledger["extraction_stats"]["raw_fallback"], 0)

    def test_alliance_bestlite_rows_split_mixed_counterparties(self):
        self.assertEqual(
            extract_alliance_party_name(
                "IB2G FND TRF CA - CA AOBFTR03092025011749 PV-25260 BESTLITE ELECTRICAL PEARLMATICS SDN BHD",
                account_holder="BESTLITE ELECTRICAL",
            ),
            "PEARLMATICS SDN BHD",
        )
        self.assertEqual(
            extract_alliance_party_name(
                "DuitNow CR Trf CA RPP250912234789219 bestlite electrical FCM BUILDERS SDN BHD bestlite electrical sdn bhd FCM BUILDERS SDN BHD",
                account_holder="BESTLITE ELECTRICAL",
            ),
            "FCM BUILDERS SDN BHD",
        )
        self.assertEqual(
            extract_alliance_party_name(
                "IB2G FND TRF CA - CA AOBFTR12092025035862 BESTLITE-JUN'25-JUL' SIM LIM TRADING BESTLITE ELECTRICAL",
                account_holder="BESTLITE ELECTRICAL",
            ),
            "SIM LIM TRADING",
        )

    def test_alliance_bestlite_annotation_infers_sdn_bhd_holder(self):
        rows = [
            {
                "date": "2025-09-02",
                "description": "DuitNow CR Trf CA RPP250902232658103 FUND TRF UOB TO AB BESTLITE ELECTRICAL FUND TRF UOB TO AB BESTLITE ELECTRICAL SDN. BHD.",
                "description_lines": [
                    "DuitNow CR Trf CA RPP250902232658103 FUND TRF UOB TO AB BESTLITE ELECTRICAL",
                    "FUND TRF UOB TO AB BESTLITE ELECTRICAL SDN. BHD.",
                ],
                "credit": 234000.0,
                "debit": 0.0,
                "balance": 1012033.98,
                "bank": "Alliance Bank",
            },
            {
                "date": "2025-09-03",
                "description": "IB2G FND TRF CA - CA AOBFTR03092025011749 PV-25260 BESTLITE ELECTRICAL PEARLMATICS SDN BHD",
                "description_lines": [
                    "IB2G FND TRF CA - CA AOBFTR03092025011749",
                    "PV-25260 BESTLITE ELECTRICAL PEARLMATICS SDN BHD",
                ],
                "credit": 5000.0,
                "debit": 0.0,
                "balance": 1017033.98,
                "bank": "Alliance Bank",
            },
            {
                "date": "2025-09-12",
                "description": "IB2G FND TRF CA - CA AOBFTR12092025035862 BESTLITE-JUN'25-JUL' SIM LIM TRADING BESTLITE ELECTRICAL",
                "description_lines": [
                    "IB2G FND TRF CA - CA AOBFTR12092025035862",
                    "BESTLITE-JUN'25-JUL' SIM LIM TRADING BESTLITE ELECTRICAL",
                ],
                "credit": 7000.0,
                "debit": 0.0,
                "balance": 1024033.98,
                "bank": "Alliance Bank",
            },
        ]

        annotate_alliance_counterparties(rows)
        names = {row["party_name"] for row in rows}

        self.assertIn("BESTLITE ELECTRICAL SDN BHD", names)
        self.assertIn("PEARLMATICS SDN BHD", names)
        self.assertIn("SIM LIM TRADING", names)

    def test_alliance_ledger_reresolves_with_manual_company_name(self):
        rows = [
            {
                "date": "2025-09-03",
                "description": "IB2G FND TRF CA - CA AOBFTR03092025011749 PV-25260 BESTLITE ELECTRICAL PEARLMATICS SDN BHD",
                "party_name": "BESTLITE ELECTRICAL PEARLMATICS SDN BHD",
                "credit": 5000.0,
                "debit": 0.0,
                "balance": 1017033.98,
                "bank": "Alliance Bank",
                "company_name": "BESTLITE ELECTRICAL",
            },
            {
                "date": "2025-09-12",
                "description": "DuitNow CR Trf CA RPP250912234789219 bestlite electrical FCM BUILDERS SDN BHD bestlite electrical sdn bhd FCM BUILDERS SDN BHD",
                "party_name": "BESTLITE ELECTRICAL FCM BUILDERS SDN BHD",
                "credit": 6000.0,
                "debit": 0.0,
                "balance": 1023033.98,
                "bank": "Alliance Bank",
                "company_name": "BESTLITE ELECTRICAL",
            },
        ]

        ledger = build_track2_counterparty_ledger(rows)
        names = {row["counterparty_name"] for row in ledger["counterparties"]}

        self.assertIn("PEARLMATICS SDN BHD", names)
        self.assertIn("FCM BUILDERS SDN BHD", names)
        self.assertNotIn("BESTLITE ELECTRICAL PEARLMATICS SDN BHD", names)
        self.assertNotIn("BESTLITE ELECTRICAL FCM BUILDERS SDN BHD", names)

    def test_agrobank_pipe_segments_prefer_non_own_company_counterparty(self):
        account_holder = "INTEGRASI ERAT SDN BHD"
        self.assertEqual(
            extract_agrobank_party_name(
                "18118595 DuitNow/Instant Dr | INTEGRASI ERAT SDN. BHD. | MK ENERALD CONSTRUCTION | RPP",
                account_holder=account_holder,
            ),
            "MK ENERALD CONSTRUCTION",
        )
        self.assertEqual(
            extract_agrobank_party_name(
                "320509698 DuitNow/Instant Cr | MK ENERALD CONSTRUCTION | INTEGRASI ERAT SDN. BHD.",
                account_holder=account_holder,
            ),
            "MK ENERALD CONSTRUCTION",
        )

    def test_agrobank_ledger_regroups_debit_and_credit_rows_by_non_own_party(self):
        rows = [
            {
                "date": "2025-06-13",
                "description": "18118595 DuitNow/Instant Dr | INTEGRASI ERAT SDN. BHD. | MK ENERALD CONSTRUCTION | RPP",
                "party_name": "INTEGRASI ERAT SDN BHD",
                "credit": 0.0,
                "debit": 143700.0,
                "balance": 42097.42,
                "bank": "Agrobank",
                "company_name": "INTEGRASI ERAT SDN BHD",
            },
            {
                "date": "2025-06-13",
                "description": "18118595 DuitNow/Instant Dr | INTEGRASI ERAT SDN. BHD. | MK ENERALD CONSTRUCTION | RPP",
                "party_name": "INTEGRASI ERAT SDN BHD",
                "credit": 0.0,
                "debit": 0.5,
                "balance": 42096.92,
                "bank": "Agrobank",
                "company_name": "INTEGRASI ERAT SDN BHD",
            },
            {
                "date": "2025-06-13",
                "description": "320509698 DuitNow/Instant Cr | MK ENERALD CONSTRUCTION | INTEGRASI ERAT SDN. BHD.",
                "party_name": "MK ENERALD CONSTRUCTION",
                "credit": 50000.0,
                "debit": 0.0,
                "balance": 92096.92,
                "bank": "Agrobank",
                "company_name": "INTEGRASI ERAT SDN BHD",
            },
            {
                "date": "2025-06-14",
                "description": "101639558 DuitNow/Instant Cr | MK ENERALD CONSTRUCTION | INTEGRASI ERAT SDN. BHD.",
                "party_name": "MK ENERALD CONSTRUCTION",
                "credit": 50000.0,
                "debit": 0.0,
                "balance": 142096.92,
                "bank": "Agrobank",
                "company_name": "INTEGRASI ERAT SDN BHD",
            },
        ]

        ledger = build_track2_counterparty_ledger(rows)
        self.assertEqual(len(ledger["counterparties"]), 1)
        group = ledger["counterparties"][0]
        self.assertEqual(group["counterparty_name"], "MK ENERALD CONSTRUCTION")
        self.assertEqual(group["transaction_count"], 4)
        self.assertEqual(group["credit_count"], 2)
        self.assertEqual(group["debit_count"], 2)
        self.assertEqual(group["total_credits"], 100000.0)
        self.assertEqual(group["total_debits"], 143700.5)

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
                    "counterparty_name": "BILL PAYMENT",
                    "transaction_count": 1,
                    "credit_count": 1,
                    "debit_count": 0,
                    "total_credits": 777777.0,
                    "total_debits": 0.0,
                    "transactions": [],
                },
                {
                    "counterparty_name": "MONTHLY CHARGE",
                    "transaction_count": 1,
                    "credit_count": 0,
                    "debit_count": 1,
                    "total_credits": 0.0,
                    "total_debits": 666666.0,
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

    def test_prepare_top_parties_excludes_bill_and_charge_cached_rows(self):
        top_parties = {
            "top_payers": [
                {"party_name": "UTILITY BILL", "total_amount": 999999.0, "transaction_count": 1},
                {"party_name": "REAL CUSTOMER", "total_amount": 1000.0, "transaction_count": 1},
                {"party_name": "BILLION TRADING", "total_amount": 900.0, "transaction_count": 1},
            ],
            "top_payees": [
                {"party_name": "BANK CHARGE", "total_amount": 888888.0, "transaction_count": 1},
                {"party_name": "REAL SUPPLIER", "total_amount": 500.0, "transaction_count": 1},
                {"party_name": "CHARGEPLUS SDN BHD", "total_amount": 400.0, "transaction_count": 1},
            ],
        }

        party_view = prepare_top_parties_for_report(top_parties, limit=10)

        self.assertEqual(
            [p["party_name"] for p in party_view["payers"]],
            ["REAL CUSTOMER", "BILLION TRADING"],
        )
        self.assertEqual(
            [p["party_name"] for p in party_view["payees"]],
            ["REAL SUPPLIER", "CHARGEPLUS SDN BHD"],
        )

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

    def test_own_party_is_not_report_related_party(self):
        filtered = filter_report_related_parties(
            [
                {"name": "LSR AGENCY", "relationship": "Affiliate"},
                {"name": "DANG YEAN LEE", "relationship": "Affiliate"},
            ],
            company_name="LSR AGENCY",
        )

        self.assertEqual(filtered, [{"name": "DANG YEAN LEE", "relationship": "Affiliate"}])

    def test_own_related_groups_promote_company_named_related_row_to_own_party(self):
        groups = build_own_related_party_groups_for_report(
            {
                "transactions": [
                    {
                        "date": "2025-03-01",
                        "description": "IBG CREDIT LSR AGENCY",
                        "amount": 100.0,
                        "type": "CREDIT",
                        "party_type": "OWN",
                        "party_name": "LSR AGENCY",
                    },
                    {
                        "date": "2025-03-02",
                        "description": "IBG TRANSFER LSR AGENCY",
                        "amount": 50.0,
                        "type": "DEBIT",
                        "party_type": "RELATED",
                        "party_name": "LSR AGENCY",
                    },
                ]
            },
            related_parties=[{"name": "LSR AGENCY", "relationship": "Affiliate"}],
            company_name="LSR AGENCY",
        )

        own_group = next(group for group in groups if group["badge_type"] == "OP")
        self.assertEqual(own_group["party_name"], "LSR AGENCY")
        self.assertEqual(own_group["credit_count"], 1)
        self.assertEqual(own_group["debit_count"], 1)
        self.assertFalse(any(group["badge_type"] == "RP" for group in groups))

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

    def test_related_party_manager_blocks_own_party_candidate(self):
        candidates = detect_related_party_candidates(
            {"counterparties": []},
            confirmed_names=set(),
            dismissed=set(),
            shared_report_data={
                "report_info": {
                    "company_name": "LSR AGENCY",
                    "related_party_candidates": [
                        {
                            "name": "LSR AGENCY",
                            "confidence": "HIGH",
                            "evidence": "Flagged by Track 2 engine",
                            "total_cr": 134171.0,
                            "total_dr": 0.0,
                        },
                        {
                            "name": "DANG YEAN LEE",
                            "confidence": "HIGH",
                            "evidence": "Flagged by Track 2 engine",
                            "total_cr": 0.0,
                            "total_dr": 100.0,
                        },
                    ],
                }
            },
            company_name="LSR AGENCY",
        )

        self.assertEqual([candidate["name"] for candidate in candidates], ["DANG YEAN LEE"])

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

    def test_report_own_party_keeps_sdn_bhd_from_ledger_source(self):
        cp_ledger = {
            "counterparties": [
                {
                    "counterparty_name": "UPELL CORPORATION SDN BHD",
                    "total_credits": 302_000.0,
                    "total_debits": 0.0,
                    "credit_count": 2,
                    "debit_count": 0,
                    "transaction_count": 2,
                    "transactions": [
                        {
                            "date": "2025-01-13",
                            "description": "DuitNow/Instant Trf PAYMENT PBB UPELL CORPORATION SDN BHD PBB UPELL CORPORATION SDN BHD",
                            "amount": 152_000.0,
                            "type": "CREDIT",
                            "counterparty_name_raw": "UPELL CORPORATION SDN BHD",
                        },
                        {
                            "date": "2025-01-20",
                            "description": "DuitNow/Instant Trf PAYMENT PBB UPELL CORPORATION SDN BHD PBB UPELL CORPORATION SDN BHD",
                            "amount": 150_000.0,
                            "type": "CREDIT",
                            "counterparty_name_raw": "UPELL CORPORATION SDN BHD",
                        },
                    ],
                }
            ]
        }

        rows = build_report_counterparty_ledger_rows(cp_ledger, company_name="UPELL CORPORATION")

        self.assertEqual(rows[0]["counterparty_name"], "UPELL CORPORATION SDN BHD")
        self.assertEqual(rows[0]["transactions"][0]["party_name"], "UPELL CORPORATION SDN BHD")

    def test_report_does_not_move_mixed_description_counterparty_to_own_party(self):
        cp_ledger = {
            "counterparties": [
                {
                    "counterparty_name": "SIN CHYE HUAT SDN BHD",
                    "total_credits": 44_508.0,
                    "total_debits": 0.0,
                    "credit_count": 3,
                    "debit_count": 0,
                    "transaction_count": 3,
                    "transactions": [
                        {
                            "date": "2024-11-07",
                            "description": "Fund Trf EB SIN CHYE HUAT SDN BH UPELL CORPORATION SCHSB",
                            "amount": 16_120.0,
                            "type": "CREDIT",
                            "counterparty_name_raw": "SIN CHYE HUAT SDN BHD",
                        },
                        {
                            "date": "2024-12-11",
                            "description": "Fund Trf EB SIN CHYE HUAT SDN BH UPELL CORPORATION SB SCHSB",
                            "amount": 10_050.0,
                            "type": "CREDIT",
                            "counterparty_name_raw": "SIN CHYE HUAT SDN BHD",
                        },
                        {
                            "date": "2025-02-12",
                            "description": "Fund Trf EB SIN CHYE HUAT SDN BH UPELL CORPORATION SD SCHSB",
                            "amount": 18_338.0,
                            "type": "CREDIT",
                            "counterparty_name_raw": "SIN CHYE HUAT SDN BHD",
                        },
                    ],
                }
            ]
        }

        rows = build_report_counterparty_ledger_rows(cp_ledger, company_name="UPELL CORPORATION")

        self.assertEqual([row["counterparty_name"] for row in rows], ["SIN CHYE HUAT SDN BHD"])
        self.assertEqual(rows[0]["transactions"][0]["party_name"], "SIN CHYE HUAT SDN BHD")

    def test_manual_company_and_account_override_moves_account_rows_to_own_party(self):
        cp_rows = [
            {
                "counterparty_name": "SUPPLIER SDN BHD",
                "total_credits": 500.0,
                "total_debits": 100.0,
                "credit_count": 1,
                "debit_count": 1,
                "transaction_count": 2,
                "transactions": [
                    {
                        "date": "2025-09-01",
                        "description": "IBG CREDIT SUPPLIER SDN BHD",
                        "amount": 500.0,
                        "type": "CREDIT",
                        "account_no": "123-456-789",
                    },
                    {
                        "date": "2025-09-02",
                        "description": "IBG DEBIT SUPPLIER SDN BHD",
                        "amount": 100.0,
                        "type": "DEBIT",
                        "account_no": "123456789",
                    },
                ],
            },
            {
                "counterparty_name": "OTHER ACCOUNT PARTY",
                "total_credits": 0.0,
                "total_debits": 75.0,
                "credit_count": 0,
                "debit_count": 1,
                "transaction_count": 1,
                "transactions": [
                    {
                        "date": "2025-09-03",
                        "description": "IBG DEBIT OTHER ACCOUNT PARTY",
                        "amount": 75.0,
                        "type": "DEBIT",
                        "account_no": "999999999",
                    },
                ],
            },
        ]

        groups = build_own_related_party_groups_for_report(
            {"transactions": []},
            related_parties=[{"name": "SUPPLIER SDN BHD", "relationship": "Affiliate"}],
            company_name="MANUAL COMPANY SDN BHD",
            counterparty_rows=cp_rows,
            manual_company_identity_override=True,
            company_account_no="123456789",
        )

        own_group = next(group for group in groups if group["badge_type"] == "OP")
        supplier_group = next(group for group in groups if group["party_name"] == "SUPPLIER SDN BHD")
        self.assertEqual(own_group["party_name"], "MANUAL COMPANY")
        self.assertEqual(own_group["credit_count"], 1)
        self.assertEqual(own_group["debit_count"], 1)
        self.assertEqual(own_group["transaction_count"], 2)
        self.assertEqual({txn["party_type"] for txn in own_group["transactions"]}, {"OWN"})
        self.assertEqual(supplier_group["badge_type"], "RP")
        self.assertEqual(supplier_group["transaction_count"], 0)

    def test_related_party_summary_excludes_manual_own_account_rows(self):
        from app import build_related_party_summary_rows_for_report

        cp_rows = [
            {
                "counterparty_name": "SUPPLIER SDN BHD",
                "total_credits": 500.0,
                "total_debits": 100.0,
                "credit_count": 1,
                "debit_count": 1,
                "transaction_count": 2,
                "transactions": [
                    {
                        "date": "2025-09-01",
                        "description": "IBG CREDIT SUPPLIER SDN BHD",
                        "amount": 500.0,
                        "type": "CREDIT",
                        "account_no": "123456789",
                    }
                ],
            }
        ]

        rows = build_related_party_summary_rows_for_report(
            [{"name": "SUPPLIER SDN BHD", "relationship": "Affiliate"}],
            {"transactions": []},
            cp_rows=cp_rows,
            company_name="MANUAL COMPANY SDN BHD",
            manual_company_identity_override=True,
            company_account_no="123456789",
        )

        self.assertEqual(rows[0]["name"], "SUPPLIER SDN BHD")
        self.assertEqual(rows[0]["transaction_count"], 0)
        self.assertEqual(rows[0]["total_credits"], 0.0)

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
