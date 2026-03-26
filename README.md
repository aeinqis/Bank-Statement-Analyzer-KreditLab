# Bank Statement Parser (Multi-bank)

Streamlit app to extract transactions from Malaysian bank statement PDFs.

## Run locally

Set HTTP Basic Auth credentials via environment variables before starting the app:

```bash
export BASIC_AUTH_USER=your_username
export BASIC_AUTH_PASS=your_password
```

Then run:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Validate against reference statements

Reference statements are stored in `Bank-Statement/`.
Run this checker to quickly verify each parser output:

```bash
python scripts/validate_reference_statements.py
```

It prints CSV-style summary columns:
- `files`
- `files_with_zero_tx`
- `total_tx`
- `invalid_dates`
- `parse_errors`
- `missing_required_keys`
- `both_debit_credit_positive`

You can also validate one bank at a time:

```bash
python scripts/validate_reference_statements.py --bank Maybank --verbose
```

> Note: `files_with_zero_tx` can be expected for image-only/scanned PDFs when OCR is unavailable.

## Railway deployment

This repo is deployable on Railway using the included `Procfile`:

```procfile
web: streamlit run app.py --server.address=0.0.0.0 --server.port=$PORT --server.headless=true
```

Recommended Railway setup:
1. Set the start command to `streamlit run app.py --server.address=0.0.0.0 --server.port=$PORT --server.headless=true` (or let Railway use `Procfile`).
2. Ensure `requirements.txt` is installed during build.
3. Use Python 3.10+ runtime.
4. Configure `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` environment variables in Railway.

## Notes

- Some statement formats are image-only PDFs. Transaction extraction for those requires OCR support (Tesseract binary) in the host environment.
- For text-based PDFs, parser extraction runs without OCR dependencies.
