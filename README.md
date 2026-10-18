# AI Investment Journal / Portfolio Analyst

**A Python project for turning broker exports into structured investment data, portfolio analytics, and eventually evidence-based AI reports.**

> **Status: Work in progress (early development).** The XTB cash-operations importer, transaction-type normalization, account/currency model, and initial automated tests are implemented. Portfolio performance calculations, a user interface, and LLM reporting are planned—not yet available.

## Why this project?

Tracking investments across multiple accounts and currencies in spreadsheets is time-consuming and error-prone. This project aims to automate that workflow: import transaction history, validate and normalize it, calculate portfolio performance, and combine the calculated results with an investment journal.

The AI component is intentionally **not** a stock-price predictor or trading advisor. Its role will be to categorize decision notes, identify recurring themes, and write readable reports grounded in verified, structured data. **Python will calculate all financial metrics; the LLM will not be responsible for financial arithmetic.**

## Current features

- Read XTB Excel (`.xlsx`) **Cash Operations** sheets, including exports with incorrect worksheet dimension metadata.
- Validate the expected column headers, timestamps, and required transaction fields.
- Parse operations into typed Python data objects using `dataclasses`, `datetime`, and `Decimal`.
- Normalize XTB operation labels (such as `Stock purchase`) into broker-independent operation types (such as `BUY`).
- Represent investment accounts with supported settlement currencies: PLN, EUR, and USD.
- Run automated tests with `pytest`, including invalid-header and missing-value scenarios.

**Current limitations:** The importer processes one Excel file at a time and currently focuses on `Cash Operations`. It does not yet reconcile open/closed positions, infer account currencies from an export, calculate portfolio returns, convert between currencies, import a whole ZIP archive, or generate AI reports.

## Planned workflow

```text
XTB export (.xlsx / ZIP)
    → Parsing and validation
    → Normalized operations and accounts
    → Persistent storage
    → Historical market data and FX rates
    → Portfolio / benchmark calculations in Python
    → Structured report input (JSON)
    → LLM-assisted journal analysis
    → Validated report and dashboard
```

## Tech stack

**Implemented:** Python 3.12, openpyxl, pytest, Git.

**Under consideration for later milestones:** Pandas, SQLite/SQLAlchemy, Streamlit, Pydantic, market-data API, LLM API. Technologies will be added only when needed.

## Getting started (Windows / PowerShell)

Clone the repository and enter its directory:

```powershell
git clone https://github.com/jLidak/ai-investment-journal.git
cd ai-investment-journal
```

Create a virtual environment and install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

To inspect your own XTB Excel export, place **one** `.xlsx` file at `data/private/xtb_sample.xlsx` and run:

```powershell
.\.venv\Scripts\python.exe scripts\inspect_xtb.py
```

This is currently a developer inspection script, **not a finished end-user import interface**. It displays worksheet names, headers, and counts of transaction types. The parsing code lives in `app/ingestion/xtb_cash.py`.

## Project structure

```text
app/
  domain/
    accounts.py           # Investment account and currency types
    operation_types.py    # Broker-independent transaction types
  ingestion/
    xtb_cash.py           # XTB Cash Operations XLSX parser
scripts/
  inspect_xtb.py          # Workbook inspection helper
tests/
  test_accounts.py
  test_operation_types.py
  test_xtb_cash.py
data/
  private/                # Local financial exports; ignored by Git
requirements.txt
.gitignore
```

## Roadmap

- [x] Inspect the real XTB XLSX structure.
- [x] Parse and validate `Cash Operations`.
- [x] Normalize operation types.
- [x] Define account and currency models (PLN/EUR/USD).
- [x] Add initial automated tests.
- [ ] Connect normalized operations to accounts and currencies.
- [ ] Support and reconcile multiple XTB worksheets/accounts.
- [ ] Store transactions and journal entries in a local database.
- [ ] Reconstruct positions and calculate cash flows and portfolio performance.
- [ ] Compare performance with an appropriate benchmark, including FX considerations.
- [ ] Add an investment journal and structured LLM reports with validation.
- [ ] Build a local dashboard and publish a synthetic demo dataset.

## Engineering principles

- **Deterministic finance:** Calculate numerical metrics in Python and test them against known examples.
- **Explicit assumptions:** Distinguish cash transfers, trades, dividends, fees, and taxes. Do not silently invent missing prices or account history.
- **Broker-independent domain:** Keep raw XTB terminology separate from internal models.
- **Defensive imports:** Fail clearly on unexpected formats instead of silently accepting incorrect data.
- **AI with evidence:** Use structured inputs/outputs, schema validation, and traceable source records for any generated insights.
- **Privacy by design:** Never commit real account exports, credentials, or API keys.

## Data privacy

Real financial exports belong in `data/private/`, which is excluded from version control. **No personal trading history is provided in this repository.** A fully synthetic dataset will be added for public demonstrations.

## AI-assisted development

AI is used as a learning and engineering assistant: to discuss trade-offs, review code, and design tests. The goal is to understand and independently verify the implemented logic rather than to ship unchecked AI-generated code.

---

*Personal learning and portfolio project. Not financial advice or an automated trading system.*
