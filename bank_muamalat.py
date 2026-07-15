# bank_muamalat.py

import re
from datetime import datetime

DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})$")
AMOUNT_RE = re.compile(r"-?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d{1,2})?|-?\.\d{1,2}")

COL_DATE = (25, 75)
COL_DESC = (75, 185)
COL_REF = (185, 295)
COL_WITHDRAWAL = (295, 395)
COL_DEPOSIT = (395, 500)
COL_BALANCE = (500, 605)
COL_DETAILS = (605, 760)

CHEQUE_RE = re.compile(r"\b(?:CHEQUE|CEK|CHQ|NO\.CEK|CHEQUE\s*/\s*REF)\b", re.I)

SYSTEM_RE = re.compile(
    r"\b(?:SERVICE\s+CHARGE|PROFIT\s+PAID|ENDING\s+BALANCE|TOTAL|BALANCE\s+B/F)\b",
    re.I,
)

FOOTER_LINE_RE = re.compile(
    r"\b(?:"
    r"EFFECTIVE\s+30\s+SEPTEMBER|"
    r"PROPRIETORSHIP\s+WILL\s+NO\s+LONGER|"
    r"I-MUAMALAT\.COM\.MY|"
    r"SEGALA\s+BILANGAN\s+DAN\s+BAKI|"
    r"ALL\s+ITEMS\s+AND\s+BALANCES|"
    r"TANDA\s+['\"]?-['\"]?\s+PADA\s+BAKI|"
    r"THE\s+SIGN\s+['\"]?-['\"]?\s+AGAINST|"
    r"SILA\s+MAKLUMKAN\s+KEPADA|"
    r"CHANGE\s+OF\s+ADDRESS|"
    r"SEBARANG\s+CEK\s+DEPOSIT|"
    r"ANY\s+CHEQUES\s+DEPOSITED|"
    r"TOTAL\s+ENDING\s+BALANCE|"
    r"CURRENT\s+I-MUAMALAT\s+ONLINE|"
    r"ONLINE\s+PORTAL|"
    r"THIS\s+STATEMENT|"
    r"PAGE\s+\d+"
    r")\b",
    re.I,
)

HEADER_LINE_RE = re.compile(
    r"\b(?:"
    r"TARIKH|DATE|PERKARA|DESCRIPTION|BUTIR-BUTIR\s+TAMBAHAN|"
    r"ADDITIONAL\s+DETAILS|WITHDRAWAL|DEPOSIT|BALANCE"
    r")\b",
    re.I,
)

FOOTER_CUT_MARKERS = [
    "EFFECTIVE 30 SEPTEMBER",
    "PROPRIETORSHIP WILL NO LONGER",
    "HTTPS:WWW.I-MUAMALAT.COM.MY",
    "HTTPS://WWW.I-MUAMALAT.COM.MY",
    "WWW.I-MUAMALAT.COM.MY",
    "SEGALA BILANGAN DAN BAKI",
    "ALL ITEMS AND BALANCES",
    "TANDA \"-\" PADA BAKI",
    "THE SIGN \"-\" AGAINST",
    "SILA MAKLUMKAN KEPADA",
    "CHANGE OF ADDRESS",
    "SEBARANG CEK DEPOSIT",
    "ANY CHEQUES DEPOSITED",
    "TOTAL ENDING BALANCE",
    "CURRENT I-MUAMALAT ONLINE",
    "ONLINE PORTAL",
    "THIS STATEMENT",
    "PAGE ",
]


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def money_to_float(value):
    value = clean_text(value)
    value = value.replace(" ", "")
    if not value or value in {".00", "0.00"}:
        return 0.0
    if value.startswith("."):
        value = "0" + value
    match = AMOUNT_RE.search(value)
    if not match:
        return 0.0

    amount = match.group(0)
    if amount.startswith("."):
        amount = "0" + amount
    elif amount.startswith("-."):
        amount = "-0" + amount[1:]
    return float(amount.replace(",", ""))


def parse_muamalat_date(value):
    value = clean_text(value).replace(" ", "").strip(".,")
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def scaled_col(col, page_width):
    if not page_width:
        return col
    if float(page_width) >= COL_DETAILS[1]:
        return col
    scale = float(page_width) / COL_DETAILS[1]
    return tuple(v * scale for v in col)


def page_columns(page):
    page_width = float(getattr(page, "width", COL_DETAILS[1]) or COL_DETAILS[1])
    return {
        "date": scaled_col(COL_DATE, page_width),
        "desc": scaled_col(COL_DESC, page_width),
        "ref": scaled_col(COL_REF, page_width),
        "withdrawal": scaled_col(COL_WITHDRAWAL, page_width),
        "deposit": scaled_col(COL_DEPOSIT, page_width),
        "balance": scaled_col(COL_BALANCE, page_width),
        "details": scaled_col(COL_DETAILS, page_width),
    }


def words_in_col(line_words, x_min, x_max):
    items = []
    for w in line_words:
        x0 = float(w["x0"])
        x1 = float(w.get("x1", x0))
        mid = (x0 + x1) / 2
        if x_min <= mid < x_max:
            items.append(w["text"])
    return clean_text(" ".join(items))


def normalize_date_text(value):
    value = clean_text(value).replace(" ", "").strip(".,")
    return value if DATE_RE.fullmatch(value) else ""


def find_date_in_line(line_words, columns):
    date_text = normalize_date_text(words_in_col(line_words, *columns["date"]))
    if date_text:
        return date_text

    sorted_words = sorted(line_words, key=lambda w: float(w["x0"]))
    if not sorted_words:
        return ""

    leftmost_x = float(sorted_words[0]["x0"])
    max_date_x = columns["ref"][0]

    for w in sorted_words[:4]:
        x0 = float(w["x0"])
        text = normalize_date_text(w["text"])
        if text and x0 <= max_date_x:
            return text

    compact_first_words = "".join(clean_text(w["text"]).strip(".,") for w in sorted_words[:3])
    if normalize_date_text(compact_first_words) and leftmost_x <= max_date_x:
        return compact_first_words

    return ""


def group_words_by_line(words, y_tol=2.5):
    lines = []

    for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
        top = float(w["top"])

        if not lines or abs(top - lines[-1]["top"]) > y_tol:
            lines.append({"top": top, "words": [w]})
        else:
            lines[-1]["words"].append(w)

    return lines


def line_text(line):
    return clean_text(" ".join(w["text"] for w in line["words"]))


def is_muamalat_footer_line(text):
    text = clean_text(text)
    return bool(text and FOOTER_LINE_RE.search(text))


def is_muamalat_header_line(text):
    text = clean_text(text)
    if not text:
        return False
    return bool(HEADER_LINE_RE.search(text) and not DATE_RE.search(text))


def sanitize_muamalat_description(description):
    text = clean_text(description)
    if not text:
        return ""

    upper_text = text.upper()
    cut_index = None
    for marker in FOOTER_CUT_MARKERS:
        marker_index = upper_text.find(marker)
        if marker_index >= 0 and (cut_index is None or marker_index < cut_index):
            cut_index = marker_index

    if cut_index is not None:
        text = text[:cut_index]

    text = re.sub(r"\s*[-=/,:;|]+\s*$", "", text)
    return clean_text(text)


MUAMALAT_PATTERNS = [
    # Cheque group
    ("CHEQUE", re.compile(r"\b(?:CHEQUE|CHQ|CEK|CHEQUE ISSUANCE|NO\.CEK)\b", re.I)),

    # Profit / system
    ("PROFIT PAID", re.compile(r"\bPROFIT PAID\b", re.I)),

    # FPX / statutory
    ("FPX", re.compile(
        r"^FPX DEBIT\s+\S+\s+\S+\s+\S+\s+(?P<party>.+)$",
        re.I,
    )),

    # Debit advice / bulk
    ("DEBIT_ADVICE", re.compile(
        r"^DEBIT ADVICE\s+\S+\s+(?P<party>.+)$",
        re.I,
    )),

    # Direct Debit
    ("DIRECT_DEBIT", re.compile(
        r"^Direct Debit\s+(?P<party>.+?)(?:\s+MBIBT|\s+E-\d|$)",
        re.I,
    )),

    # Fund Transfer debit + service charge with same counterparty
    ("FUND_TRANSFER", re.compile(
        r"^(?:BILL PAYMENT DEBIT|SERVICE CHARGE/MISC)\s+Fund Transfer\s+(?P<body>.+)$",
        re.I,
    )),

    # JomPAY / bill payment without Fund Transfer
    ("BILL_PAYMENT", re.compile(
        r"^BILL PAYMENT DEBIT\s+\S+\s+\S+\s+(?P<party>.+)$",
        re.I,
    )),

    # DuitNow
    ("DUITNOW", re.compile(
        r"^(?=.*\bDUITNOW\b)(?:DUITNOW TRANSFER\s+)?(?:\S+\s+){1,4}(?:DuitNow\s+.*?)?(?P<party>[A-Z0-9&().'\-/ ]+(?:SDN\.?\s*BHD|SDN\s+BHD|BHD|BERH|MAXIS|UNIFI|IWK|AIR SELANGOR|TENAGA NASIONAL|PERBADANAN|LEMBAGA).*)$",
        re.I,
    )),

    # Transfer / salary / misc
    ("TRF_SAL_MISC", re.compile(
        r"^(?:TFR/SAL/MISC|TRF/SAL/MISC/AFT)\s+(?P<body>.+)$",
        re.I,
    )),

    # Plain service charge
    ("BANK_CHARGES", re.compile(r"^SERVICE CHARGE/MISC\b", re.I)),
]


def _normalize_muamalat_company_suffix(value):
    tokens = []
    for token in clean_text(value).upper().split():
        token_core = token.strip(" .,-")
        if not token_core:
            continue
        if token_core in {"SN", "SND", "SD", "SDN"}:
            token = "SDN"
        elif token_core in {"BH", "BDH", "B", "BHD"} and any(existing == "SDN" for existing in tokens):
            token = "BHD"
        else:
            token = token_core
        tokens.append(token)

    if "SDN" in tokens:
        sdn_index = tokens.index("SDN")
        if sdn_index + 1 < len(tokens) and tokens[sdn_index + 1] == "BHD":
            tokens = tokens[: sdn_index + 2]
        else:
            tokens = tokens[: sdn_index + 1] + ["BHD"]

    return clean_text(" ".join(tokens)) or "UNKNOWN"


MUAMALAT_DETAIL_PREFIX_PATTERNS = [
    re.compile(r"^(?:\d+(?:ST|ND|RD|TH)\s+)+", re.I),
    re.compile(r"^\d+[A-Z]\s+", re.I),
    re.compile(r"^(?:INV(?:OICE)?(?:\s+NO\.?|\s+NO:|\s+NO\s+KP|\.?)?|NO\.?|NO:|YOUR\s+REF:?|REF:?)\b\s*[:./-]?\s*(?:KP\s+)?(?:[A-Z]*\d[A-Z0-9/.,&:-]*\s+)*", re.I),
    re.compile(r"^(?:JAN|FEB|MAR|MARCH|APR|MAY|JUN|JUNE|JUL|AUG|AUGUST|SEP|SEPT|OCT|OCTOBER|NOV|DEC)\s+(?:INV(?:OICE)?\s+)?", re.I),
    re.compile(r"^(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2,4}\s+", re.I),
    re.compile(r"^\(\d+(?:ST|ND|RD|TH)\)\s+", re.I),
    re.compile(r"^\(?\d+\s*PAX\)?\s+", re.I),
    re.compile(r"^\([A-Z0-9]+\s+[A-Z0-9]+\s+[A-Z]{2,}\d+\s+", re.I),
    re.compile(r"^(?:HP[- ]?)?[A-Z]{2,6}\s+\d{2,}\s+", re.I),
    re.compile(r"^(?:[A-Z]{1,8}[-_/]*)?\d[A-Z0-9()./,&:-]*(?:\s*&\s*[A-Z]{0,8}\d[A-Z0-9()./,&:-]*)*\s+", re.I),
    re.compile(
        r"^(?:"
        r"CLAIM|PAYMENT|REPAYMENT|PARTIAL\s+PAYMENT\s+LOAN|PARTIAL\s+PAYMENT|"
        r"EXP(?:ENSES?)?(?:\s+MIRI)?|NONE|"
        r"SETTLEMENT(?:\s+SHAPADU)?\s+STAKEHOLDER(?:\s+PAYMENT)?|"
        r"BIZ\s+DEVELOPMENT\s+FUND|ENTERTAINMENT|IHYA\s+RAMADAN\s+TAWFIQ|"
        r"AMAN\s+NUKLEUS\s+PROJECT|"
        r"MAJLIS\s+ARAFAH|ADVANCE\s+OUTSTATION|OUTSTATION(?:\s+EXP(?:ENSES?)?(?:\s+MIRI)?)?|"
        r"OFFICE\s+SUPPLIES|OFFICE\s+EQUIPMENT|OFFICE\s+MAINTENANCE|SINK\s+&\s+INSTALLATION|"
        r"FLIGHT\s+(?:TICKET|TIX)|FOR\s+(?:AZ\s+HOTEL|HARDWARE|PROJECT\s+GUARDIAN|FUNDING\s+SOCIETIES)|"
        r"FUND(?:\s+OVERHEAD(?:\s+[A-Z0-9]+)?|\s+PUNB\s+TO\s+BKR(?:\s+\d+)?|\s+AK\s+PROJECT)?|"
        r"INSURAN\s+ROADTAX(?:\s+[A-Z0-9]+)?|TRANSFER\s+TO\s+BMMB|TO\s+BMMB|"
        r"PAY\s+FOR\s+SZ\s+&\s+LOAN|"
        r"PURCHASE(?:\s+OF\s+LAPTOP|\s+OF|\s+FOR)?|OF\s+MACBOOK|RENEW\s+INSURANCE|ROADTAX(?:\s+[A-Z0-9]+)?|"
        r"STAFF\s+REMUNERATION|TAJAAN\s+RAMADAN|PHONE\s+ALLOWANCE(?:\s+JUN)?|PETTY\s+CASH|"
        r"MIRI\s+&\s+LABUAN\s+VESSEL|MIRI\s+AJANG|MIRI\s+INSPECTIO|MIRI|VUNGTAU(?:,?\s*VN)?|"
        r"VSAT,?\s+BMS\s+TMS,?\s+DEMOB|MEALS,?\s+MGO\s+AND\s+FW,?\s+I|"
        r"KURMA\s+MAHNAZ|LAMP\s+&\s+BULB|HARDDISK|PETROCHEM\s+FR|NILAM\s+BESTARI\s+ENTERP|"
        r"CHIN\s+CHUN\s+HARDWARE|PUNB(?:\s+TO\s+BKR)?|INSTALMENT\s+PUNB|"
        r"DISBURSE(?:\s+\([^)]*\))?|(?:DISBURSEM?ENT|EMENT)\s+\d{2}\.\d{2}\.\d{4}\s+\S+\s+RESIDE|"
        r"SRF\s+\d+K|SRF\s+&\s+AMAN\s+NUKLEUS|OCL\s+REPAYMENT|OCL|LOAN(?:\s+(?:SRF|OCL|AMBANK|AMBAN))?|"
        r"OVERHEAD|PAY\s+FOR\s+SURAU\s+ALABRA|E\s+PEROLEHAN|GRAB|"
        r"AF\s*-?\s*AJANG\s+HORM|CD\s*-?\s*LUMUT|AJANG|CR|TR|FARIS"
        r")\b[\s,./&:-]*",
        re.I,
    ),
]


def _strip_muamalat_detail_prefixes(value: str) -> str:
    party = clean_text(value).upper().strip(" ,.-")
    previous = None
    while party and previous != party:
        previous = party
        for pattern in MUAMALAT_DETAIL_PREFIX_PATTERNS:
            party = pattern.sub("", party, count=1).strip(" ,.-")
        party = re.sub(r"^[&/.,:-]+\s*", "", party).strip(" ,.-")
    return party


def clean_muamalat_party_name(value: str) -> str:
    party = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-").upper()

    party = re.sub(r"\bSDN\.?\s*BHD\.?\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*BH\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*B\b", "SDN BHD", party, flags=re.I)

    # Remove common references / remarks before party.
    party = re.sub(
        r"^(?:INV(?:OICE)?|INV NO\.?|CLAIM|PAYMENT|REPAYMENT|FUND|JV|EMS|HP|SR-|QM-|TT|PE-|MBINV|G2EVSB|HE23|L-\d+|B\d+\w+|\d{5,}|[:\-/&.,\s])+",
        "",
        party,
        flags=re.I,
    )

    party = _strip_muamalat_detail_prefixes(party)
    party = re.sub(r"^\*\d+(?:[*&./:-]\d+)*\s*", "", party)
    party = re.sub(r"^(?:[*&./:-]*\d+){2,}[*&./:-]*\s+", "", party)
    party = re.sub(r"\bPIGMY[A-Z0-9]+\b", "", party)
    party = re.sub(r"\b\d{6,}\b", "", party)
    party = _strip_muamalat_detail_prefixes(party)
    party = re.sub(r"\s{2,}", " ", party).strip(" ,.-")
    tokens = party.split()
    for size in range(min(4, len(tokens) // 2), 0, -1):
        if tokens[:size] == tokens[size:size * 2]:
            party = " ".join(tokens[size:])
            break

    return _normalize_muamalat_company_suffix(party) if party else "UNKNOWN"


def extract_party_from_body(body: str) -> str:
    body = clean_muamalat_party_name(body)

    # Person names
    person = re.search(
        r"([A-Z][A-Z .']+\s+(?:BIN|BINTI|A/L|A/P)\s+[A-Z .']+)$",
        body,
        re.I,
    )
    if person:
        return clean_muamalat_party_name(person.group(1))

    # Company / organisation names near the end
    company = re.search(
        r"([A-Z0-9&().'\-/ ]+\b(?:SDN BHD|BHD|ENTERPRISE|RESOURCE|RESOURC|SERVIC|SERVICES|LOGISTIC|LOGISTICS|ENGINEERING|TRADING|AGENCIES|OFFSHORE|EMPIRE|CORONA|CREDIT|UNIFI|MAXIS|TENAGA NASIONAL|AIR SELANGOR|IWK|PERBADANAN|LEMBAGA|PERTUBUHAN|KUMPULAN WANG SIMPAN|ZAKAT SELANG|HASIL DALAM))$",
        body,
        re.I,
    )
    if company:
        return clean_muamalat_party_name(company.group(1))

    return body


def extract_muamalat_party_name(description):
    desc = re.sub(r"\s+", " ", str(description or "")).strip()

    if not desc:
        return "UNKNOWN"

    for label, pattern in MUAMALAT_PATTERNS:
        match = pattern.search(desc)

        if not match:
            continue

        if label == "CHEQUE":
            return "CHEQUE"

        if label in {"PROFIT PAID", "BANK_CHARGES"}:
            return label.replace("_", " ")

        if "party" in match.groupdict():
            return clean_muamalat_party_name(match.group("party"))

        if "body" in match.groupdict():
            return extract_party_from_body(match.group("body"))

    return "UNKNOWN"


def parse_transactions_bank_muamalat(pdf, source_file):
    transactions = []

    for page_num, page in enumerate(pdf.pages, start=1):
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
        columns = page_columns(page)
        lines = group_words_by_line(words)

        rows = []

        for line in lines:
            line_words = line["words"]
            date_text = find_date_in_line(line_words, columns)

            if not date_text:
                continue

            rows.append({
                "top": line["top"],
                "date": date_text,
            })

        for idx, row in enumerate(rows):
            top = row["top"]
            bottom = rows[idx + 1]["top"] if idx + 1 < len(rows) else page.height

            row_words = []
            for line in lines:
                if not (top - 1 <= float(line["top"]) < bottom - 1):
                    continue

                text = line_text(line)
                is_first_row_line = abs(float(line["top"]) - top) <= 2.5
                if is_muamalat_footer_line(text) and not is_first_row_line:
                    break
                if is_muamalat_header_line(text):
                    continue

                row_words.extend(line["words"])

            row_words_without_date = [
                w for w in row_words
                if not normalize_date_text(w["text"])
            ]

            desc = words_in_col(row_words_without_date, *columns["desc"])
            ref = words_in_col(row_words_without_date, *columns["ref"])
            withdrawal = words_in_col(row_words_without_date, *columns["withdrawal"])
            deposit = words_in_col(row_words_without_date, *columns["deposit"])
            balance = words_in_col(row_words_without_date, *columns["balance"])
            details = words_in_col(row_words_without_date, *columns["details"])

            if desc.upper() in {"TOTAL", "ENDING BALANCE"}:
                continue

            debit = money_to_float(withdrawal)
            credit = money_to_float(deposit)
            bal = money_to_float(balance)

            full_description = sanitize_muamalat_description(
                " ".join(x for x in [desc, ref, details] if x)
            )
            if not full_description:
                continue

            iso_date = parse_muamalat_date(row["date"])
            if not iso_date:
                continue

            transactions.append({
                "date": iso_date,
                "description": full_description,
                "party_name": extract_muamalat_party_name(full_description),
                "debit": debit,
                "credit": credit,
                "balance": bal,
                "page": page_num,
                "bank": "Bank Muamalat",
                "source_file": source_file,
            })

    return transactions