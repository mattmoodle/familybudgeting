# Family Budget Offline

> Current release: **v0.8.0**. Includes the v0.7 recurrence controls plus local backup, restore validation and data export.

## Navigation

The interface is organized into dedicated pages accessible from the top menu: **Panoramica**, **Budget**, **Transazioni**, **Da verificare**, **Ricorrenze**, **Analisi**, and **Documenti**. This keeps focused workflows separate while preserving the same local SQLite data and offline operation. The **Ricorrenze** page supports filters by merchant, category, cadence, type and status; click a column header to sort the visible recurrences.

## Supported statement formats

PDF import includes dedicated local parsers for BBVA, BCC Roma, BPER, PayPal, Satispay and Numia formats, alongside generic CSV/XLSX import. BBVA **Ultime transazioni** PDFs are read as multi-line rows with booking date, value date, description and signed amount.

## Review queue

Saving an item in **Da verificare** records the selected category and marks the transaction as reviewed. You may intentionally leave it as **Uncategorized**; it will no longer return to the review queue. No automatic rule is created for an Uncategorized choice.

A privacy-first, fully local family budgeting platform built as a portfolio-grade FastAPI project.

## Why this project is different

The application does not treat every imported row as a new expense. It keeps a **canonical local ledger** and separates:

- source-file duplicates;
- repeated transaction rows;
- internal money movements between owned accounts;
- wallet funding (PayPal/Satispay);
- card settlement movements;
- actual household income and spending.

Original rows are retained for auditability. Flags and manual classifications can be changed without modifying the source statements.

## Stack

- FastAPI — HTTP API + local web UI
- Pydantic — API schemas/settings validation
- SQLAlchemy 2 — persistence and query layer
- SQLite — single-file local database
- pypdf — text-based PDF statements
- openpyxl — XLSX statements
- Jinja2 + local CSS — no remote frontend assets
- pytest + Ruff — test/lint tooling

No cloud service, telemetry SDK, CDN, remote AI API, or external database is used.

## Architecture

```text
app/
├── api/                 # FastAPI routes / transport layer
├── core/                # settings
├── db/                  # engine, sessions, declarative base
├── models/              # SQLAlchemy persistence entities
├── schemas/             # Pydantic I/O contracts
├── services/
│   ├── importers/       # pluggable PDF/CSV/XLSX adapters
│   ├── classification.py
│   ├── reconciliation.py
│   ├── analytics.py
│   ├── budgeting.py     # monthly budgets, variance and end-of-month projection
│   └── import_service.py
├── static/              # fully local UI assets
└── templates/           # local server-rendered dashboard
```

The service layer is deliberately independent from HTTP so import/reconciliation logic can later be reused by a CLI, desktop shell, scheduled local job, or test suite.

## Local-only privacy model

The server binds to `127.0.0.1` by default. Imported files are copied into `data/inbox`, processed locally, then moved to `data/archive`. SQLite is local. The UI uses no CDN resources.

The desktop launcher runs Uvicorn with local reload enabled. The dashboard also provides **Riavvia app**, which applies code updates locally without exposing the service to the network.

For a stricter machine-level guarantee, block outbound traffic for the Python executable with the host firewall. The application itself contains no outbound HTTP client.

## Start from zero (Windows, Linux and macOS)

This guide assumes no previous Python or project setup. The application requires **Python 3.11 or newer** and runs entirely on the local computer.

### 1. Obtain the project

Extract the ZIP into a folder you control, then open a terminal **inside the extracted project folder** (the folder containing `pyproject.toml` and `README.md`). Do not run the commands from inside the ZIP preview.

### 2. Install Python 3.11+

#### Windows

1. Download the current Python 3 installer from [python.org/downloads](https://www.python.org/downloads/windows/).
2. Run the installer and tick **Add Python to PATH** before selecting **Install Now**.
3. Close and reopen PowerShell, then verify:

```powershell
py --version
py -m pip --version
```

If `py` is unavailable but `python` works, use `python` in the following commands instead of `py`.

#### macOS

Download the universal macOS installer from [python.org/downloads](https://www.python.org/downloads/macos/) and complete the installation. Then open Terminal and verify:

```bash
python3 --version
python3 -m pip --version
```

If you use Homebrew, the equivalent installation is `brew install python`.

#### Linux (Ubuntu/Debian)

Open a terminal and install Python plus the virtual-environment package:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
python3 --version
```

For Fedora use `sudo dnf install python3 python3-pip`; for other distributions, install the equivalent `python3`, `venv` and `pip` packages with the system package manager. Ensure the reported Python version is at least 3.11.

### 3. Create and activate a virtual environment

The virtual environment keeps this project's libraries separate from the rest of the computer.

**Windows PowerShell**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, run this once for the current terminal and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

**Windows Command Prompt**

```bat
py -m venv .venv
.venv\Scripts\activate.bat
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

When activation succeeds, the prompt normally begins with `(.venv)`. To leave the environment later, run `deactivate`.

### 4. Install dependencies

With the environment active, install the application and its development tools:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On macOS/Linux where `python` is not defined, use `python3` for both commands. The first installation downloads the required Python packages; after that, normal use of Family Budget Offline does not require Internet access.

### 5. Optional local configuration

The defaults work without configuration. To create a private local settings file, copy the example:

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
cp .env.example .env
```

`.env` is ignored by Git. It can be used to override settings such as `BUDGET_DATABASE_URL` or `BUDGET_DATA_DIR`; do not share it if it contains personal paths.

### 6. Start FastAPI

From the project folder, with the virtual environment active:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For development, automatically reload after code changes:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Keep that terminal open while using the application. Stop the server with `Ctrl+C`.

### 7. Open the dashboard and API documentation

With the server running, open these local addresses in a browser:

| Address | Purpose |
|---|---|
| `http://127.0.0.1:8000/` | Family Budget Offline dashboard |
| `http://127.0.0.1:8000/docs` | Swagger UI: interactive REST API documentation and request tester |
| `http://127.0.0.1:8000/redoc` | ReDoc API reference |
| `http://127.0.0.1:8000/openapi.json` | OpenAPI JSON specification |

Before importing statements, create an account from the dashboard or through Swagger with `POST /api/accounts`.

### 8. Run tests and lint checks

With the virtual environment active and the server stopped, run:

```bash
python -m pytest -q
python -m ruff check .
```

The test suite uses local synthetic data and does not call external services.

## Project structure

```text
family_budget/
├── app/                         # FastAPI application
│   ├── api/                     # Routes and HTTP transport
│   ├── core/                    # Local settings
│   ├── db/                      # SQLite engine, sessions and compatibility migrations
│   ├── models/                  # SQLAlchemy entities
│   ├── schemas/                 # Pydantic API contracts
│   ├── services/                # Import, classification, reconciliation and analytics logic
│   │   └── importers/           # CSV, XLSX and institution-specific PDF parsers
│   ├── static/                  # Local CSS/JavaScript assets
│   └── templates/               # Local server-rendered pages
├── data/
│   ├── inbox/                   # Files awaiting processing
│   ├── archive/                 # Locally archived imports
│   └── exports/                 # Locally generated exports
├── scripts/                     # Utility scripts, including demo-data creation
├── tests/                       # Pytest suite
├── family_budget.db             # Local SQLite ledger (personal financial data)
├── .env.example                 # Optional configuration template
├── pyproject.toml               # Dependencies and tooling configuration
└── README.md                    # Project documentation
```

Do not delete `family_budget.db` unless you deliberately want to start from an empty ledger. Back it up before moving, resetting or experimenting with the project.

## Basic troubleshooting

| Symptom | What to do |
|---|---|
| `python` / `py` command not found | Reinstall Python, enabling **Add Python to PATH** on Windows; then close and reopen the terminal. On macOS/Linux try `python3`. |
| `Python 3.10` or older | Install Python 3.11+ and recreate `.venv` with that interpreter. |
| `No module named ...` | Activate `.venv`, then run `python -m pip install -e ".[dev]"` again. |
| PowerShell refuses `Activate.ps1` | Use the temporary `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` command shown above, or use Command Prompt. |
| Browser cannot connect to port 8000 | Confirm the server terminal is still running; then open exactly `http://127.0.0.1:8000/`. |
| `Address already in use` | Stop the other process using port 8000, or start with `--port 8001` and open `http://127.0.0.1:8001/`. |
| Import does not recognize a scanned PDF | The generic PDF importer requires selectable text. Convert/export the statement to CSV/XLSX or add a local OCR workflow such as Tesseract. |
| Need a clean test database | Make a copy of `family_budget.db` first, then remove or rename it while the server is stopped; the application will create an empty local database on next startup. |

## Offline notes and data safety

- The dashboard, SQLite database, imported files, exports and static assets are stored locally.
- FastAPI binds to `127.0.0.1` in the documented commands, so it is reachable only from the same computer.
- No cloud service, telemetry, CDN, remote font, remote AI API or outbound application HTTP client is used.
- Installing Python and project dependencies may require Internet access once. After installation, operating the application does not.
- Treat `family_budget.db`, `data/archive/` and generated exports as sensitive financial data. Keep regular copies in a trusted local or encrypted location.
- Do not start the service with `--host 0.0.0.0` unless you first add appropriate authentication and network protections.

## Quick start

Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Interactive API documentation is at `/docs`.

Create an account first in `/docs` using `POST /api/accounts`, e.g.:

```json
{"name":"BPER Current Account","account_type":"bank","currency":"EUR"}
```

Supported account types: `bank`, `card`, `paypal`, `satispay`, `cash`, `other`.

## Statement import

`POST /api/imports` accepts `.csv`, `.xlsx`, `.xlsm`, and text-based `.pdf` files.

CSV/XLSX importers identify common headings such as date/data, description/causale, amount/importo, debit/addebiti, credit/accrediti. The PDF importer is intentionally a generic fallback because bank PDF layouts are institution-specific.

### Production extension point for PDFs

Create a parser such as:

```text
app/services/importers/bper_pdf.py
app/services/importers/bcc_card_pdf.py
app/services/importers/paypal_pdf.py
```

and choose it in `registry.py`. This keeps institution-specific parsing isolated from the domain logic.

Scanned PDFs are not sent to an OCR service. If needed, add a **local-only** OCR adapter (for example Tesseract installed on the machine).

## Classification

Classification has three levels:

1. manual correction — confidence 1.0;
2. persistent user rule — confidence 0.99;
3. built-in keyword heuristic — confidence 0.85;
4. uncategorized fallback — confidence 0.10.

`PATCH /api/transactions/{id}` can update the category. Set `create_rule=true` to convert a correction into a reusable local rule.

The seed rules already include examples discussed for the family dataset such as Euro Futura, restaurants, Tokyo/Japan travel, Cheerz/SHOPSI, IKEA, Prima insurance, parking, groceries and PagoPA.

## Duplicate and transfer reconciliation

### Exact/repeated row

A stable fingerprint is based on account, date, amount and normalized description. Matching rows are marked as duplicates and excluded from analytics.

### Internal transfer

Opposite amounts found on two owned accounts within a configurable date window are paired as an internal transfer. Both movements are excluded from spending/income metrics.

### Wallet/card funding fallback

A conservative description-based fallback identifies typical bank-side funding/settlement rows such as PayPal, Satispay, card, giroconto and top-up movements. The original transaction remains stored.

For a mature v2, add a reconciliation strategy interface supporting aggregate card-statement settlement matching (e.g. one bank payment against a monthly card statement total).

## Analytics

Available endpoints:

- `GET /api/analytics/summary`
- `GET /api/analytics/by-category`
- `GET /api/analytics/suggestions`

The saving suggestion engine is deliberately explainable and deterministic: it computes average monthly discretionary-category spend and proposes modest percentage reductions. No transaction data is sent to an LLM.

## Demo

```bash
python scripts/create_demo_data.py
```

This creates two local accounts, sample income/expenses, and a bank-to-PayPal internal transfer to demonstrate reconciliation.

## Tests

```bash
pytest -q
ruff check .
```

## Portfolio roadmap

Recommended next iterations:

1. recurring-pattern manual overrides and confirmations;
2. local encrypted backup/export;
3. aggregate credit-card settlement reconciliation;
4. optional local ML classifier trained only on manual corrections;
5. Alembic migrations once the schema begins evolving;
6. packaged desktop launcher (e.g. PyInstaller) while retaining the FastAPI architecture;
7. CI with synthetic data only.

## Security note

This is designed for single-user localhost use. Do not bind it to `0.0.0.0` or expose it to a LAN/Internet without adding authentication, CSRF protections, secure file controls, and a proper deployment model.

## Institution-specific importers

The PDF registry auto-detects supported statements before falling back to the generic parser:

- **BCC Roma / Movimenti Globali** — signed bank-account movements, including PayPal SDDs and Satispay funding.
- **Numia / Carta BCC** — purchase date + posting date statements, including the current table-based Credit MC layout and multiline FX rows. Values are retained in the application's accounting convention (`expense < 0`, `income/refund > 0`).
- **BPER** — monthly statements with explicit `D/A` sign columns and the current Relax Banking `Contabilizzato` table layout, preserving the signed amount printed by the bank.
- **PayPal** — transaction history grouped by currency; PayPal transaction IDs are retained in the description/raw audit data.
- **PayPal CSV** — the Italian transaction-history export is auto-detected and imports the net amount, currency, transaction ID and counterparty when present. Prefer this format to PDF whenever it is available.
- **Satispay** — transaction lists with Italian month names and Satispay UUIDs, including PDF exports where the euro glyph is encoded as a replacement character; bank funding and savings/investment-pocket movements are treated as internal transfers.

### Safety-first parsing

Financial imports intentionally prefer **skipping an ambiguous row over inventing a sign**. In particular, some older PDF bank statements visually encode debit/credit using column position that may be lost by PDF text extraction. When that happens, export `Movimenti Globali`, CSV, or XLSX from the bank when available. The original row is always retained in `raw_data` for auditability.

### Reconciliation semantics

The application distinguishes:

1. **Duplicate rows** — the same source transaction imported more than once.
2. **Internal transfers** — money moving between owned accounts/wallets (bank ↔ PayPal, bank ↔ Satispay, card settlement, savings pockets).
3. **Real spending/income** — included in analytics.

This is important for wallet-backed payments: the merchant purchase remains the real expense, while the bank funding transaction is excluded from spending totals.

## Dashboard locale (v0.2)

La home `http://127.0.0.1:8000/` è ora una dashboard operativa completamente locale e senza dipendenze frontend esterne.

Funzioni disponibili:

- KPI di entrate, spese, cash flow netto e tasso di risparmio;
- filtri globali opzionali per intervallo di date, conto e categoria: puoi lasciare vuoti uno o tutti i campi;
- pagina **Transazioni** con ricerca per testo, filtri su importo e stato, paginazione da 20/50/100 righe e azioni multiple sulle righe selezionate (categoria, inclusione nelle analisi e segnalazione sospetta);
- grafico mensile entrate/spese/saldo realizzato con HTML/CSS/JavaScript locale;
- riepilogo delle principali categorie di spesa;
- creazione dei conti direttamente dalla UI;
- importazione PDF/CSV/XLSX dalla dashboard con esito immediato;
- pagina **Documenti importati** con file, conto, data/ora, modalità, stato e accesso alle revisioni Human-check aperte;
- eliminazione esplicita di un singolo import: rimuove file locale, staging Human-check e transazioni create dal batch, così lo stesso estratto può essere importato nuovamente da zero; regole, categorie e preferenze ricorrenti locali restano preservate;
- coda **Da verificare** per transazioni non classificate o con confidenza < 75%;
- indicazione del conto associato per ogni movimento nella coda **Da verificare**;
- correzione inline della categoria e creazione opzionale di una regola automatica riutilizzabile;
- creazione immediata di una nuova categoria dalla coda **Da verificare** o dalla correzione Human-check;
- controllo esplicito **Conta nel budget** per ogni transazione (e in bulk): puoi mantenere una riga come dettaglio storico ma escluderla da budget e analisi per evitare doppi conteggi tra carta/PayPal e relativo addebito sul conto;
- indicatori visivi per duplicati, trasferimenti interni ed elementi esclusi;
- suggerimenti di risparmio deterministici basati sui dati locali.

Nessuna libreria JavaScript, font, analytics o CDN viene caricata da Internet: il browser comunica soltanto con il server FastAPI in esecuzione sul computer locale.

## Human-check import (v0.3)

L'import può essere avviato in due modalità dalla dashboard:

- **Standard** — parsing, classificazione, riconciliazione e inserimento immediato nel ledger.
- **Human-check** — parsing in un'area di staging; nessuna riga viene conteggiata finché la revisione non è conclusa.

Il workflow Human-check è volutamente a due passaggi:

1. **Tinder-like review** — per ogni riga vengono mostrati affiancati il contenuto originale dell'estratto e il risultato strutturato del parser (data, descrizione, merchant normalizzato, importo, categoria e confidenza). L'utente risponde **Sì/No** usando i pulsanti, le frecce da tastiera o lo swipe su mobile.
2. **Correction pass** — al termine della prima passata vengono riproposte, una alla volta, soltanto le righe flaggate **No**. L'utente può correggere data, descrizione, importo, categoria e aggiungere una nota.

Solo dopo che ogni elemento è `accepted` o `corrected`, il pulsante finale esegue il commit nel ledger canonico e avvia la normale riconciliazione anti-doppio-conteggio. In questo modo una revisione interrotta a metà non inquina KPI, grafici o suggerimenti.

Endpoint principali:

- `GET /human-check/{batch_id}` — interfaccia di revisione;
- `GET /api/human-check/{batch_id}/items` — staging rows;
- `PATCH /api/human-check/items/{id}/decision` — Sì/No;
- `PATCH /api/human-check/items/{id}/correction` — correzione dettagliata;
- `POST /api/human-check/{batch_id}/finalize` — commit atomico nel ledger.

## Local learning & merchant normalization (v0.3)

Il classificatore ora lavora su una gerarchia esplicita e completamente offline:

1. **regole persistenti create dall'utente** — priorità massima, confidenza 0.99;
2. **nearest-neighbour locale** sulle transazioni corrette manualmente — apprende merchant simili senza inviare dati fuori dal computer;
3. **euristiche built-in** — keyword note e merchant normalizzati;
4. **fallback Uncategorized** — bassa confidenza, quindi entra nella review queue.

Prima della classificazione viene estratta una identità merchant più stabile rimuovendo rumore tipico dei sistemi di pagamento (POS, carta, PayPal/Satispay, riferimenti, IBAN, date e codici variabili). Alias noti come `IKEA Italia Retail`, `Amazon Marketplace` o `Euro Futura SRL` confluiscono in un merchant canonico.

Il classificatore locale non usa reti, API, telemetria né modelli remoti. Le correzioni Human-check marcate come manuali diventano esempi utili per classificare movimenti simili futuri.

### Upgrade da v0.2

La startup include una piccola migrazione SQLite idempotente per aggiungere `import_mode` e `merchant` a un database v0.2 già esistente. Il nuovo staging Human-check viene creato tramite SQLAlchemy. Una futura versione potrà sostituire questo meccanismo con Alembic quando il numero di migrazioni crescerà.

## Recurring expenses, subscriptions & forecast (v0.4)

La dashboard rileva ora automaticamente spese ricorrenti senza servizi cloud o calendari esterni.

Il detector raggruppa le uscite per **merchant normalizzato + categoria** e considera una sequenza ricorrente solo quando trova almeno tre occorrenze con una cadenza sufficientemente regolare. Le cadenze riconosciute sono settimanale, bisettimanale, mensile, bimestrale, trimestrale, semestrale e annuale.

Per ogni ricorrenza vengono calcolati:

- frequenza stimata e intervallo canonico;
- importo medio;
- variabilità degli importi;
- numero di occorrenze osservate;
- confidenza della rilevazione;
- ultima occorrenza e prossima data prevista.

### Fixed, variable, occasional

Le spese vengono inoltre suddivise in tre classi interpretabili:

- **fixed** — ricorrenza regolare con importo stabile (deviazione media <= 12%); tipicamente abbonamenti, rate, premi o servizi periodici;
- **variable** — ricorrenza temporale regolare ma importo variabile, ad esempio acquisti settimanali presso lo stesso supermercato;
- **occasional** — movimenti che non presentano una ricorrenza sufficientemente forte.

La classificazione è derivata dai dati e non modifica la categoria contabile originale della transazione.

### Forecast locale

Il sistema proietta le ricorrenze osservate nei successivi 60 giorni (configurabile via API fino a 366 giorni). Per dataset storici la previsione viene ancorata all'ultima data disponibile nel ledger, rendendo l'analisi riproducibile e testabile.

Nuovi endpoint:

- `GET /api/analytics/recurring`
- `GET /api/analytics/forecast?days=60`
- `GET /api/analytics/cost-structure`

La home mostra una tabella **Ricorrenze e abbonamenti**, la struttura dei costi e l'elenco delle prossime uscite previste. Il nome di ogni uscita prevista è cliccabile: un popup locale mostra data, importo, categoria, frequenza e i movimenti storici che supportano la previsione, con accesso diretto alla ricerca nella pagina Transazioni.

### Safety-first detection

Il detector privilegia i falsi negativi rispetto ai falsi positivi: sequenze troppo irregolari vengono lasciate tra le spese occasionali invece di essere presentate come abbonamenti. Questo è intenzionale perché le previsioni finanziarie devono essere spiegabili e correggibili.


## Monthly budgets and variance (v0.5)

The dashboard now includes a persistent monthly budget planner. A budget is stored locally per **month + category**, so the household can change limits over time without rewriting historical months.

Features:

- set or update a monthly ceiling for any spending category;
- compare budget vs actual spending;
- show remaining budget and percentage consumed;
- project month-end spending with a hybrid model that separates recurring expenses from the non-recurring daily run rate;
- classify each category as `on_track`, `warning`, `risk`, `over`, or `unbudgeted`;
- copy the previous month's budgets into a new month without overwriting values that were already customized;
- keep unbudgeted real spending visible rather than silently hiding it.

Budget analytics deliberately exclude duplicate rows and transactions excluded from analytics, using the same canonical-ledger rules as the rest of the application. For completed months, projected spending equals the final actual amount.

New endpoints:

- `PUT /api/budgets` — create/update a category budget;
- `DELETE /api/budgets/{month}/{category}` — remove a budget;
- `POST /api/budgets/copy` — copy missing budgets from another month;
- `GET /api/analytics/budget?month=YYYY-MM` — budget/actual/projection report.


## Hybrid explainable budget forecast (v0.6)

The monthly budget projection now combines two independent signals instead of blindly extrapolating every euro spent so far:

1. **non-recurring run-rate** — only the variable/occasional portion observed so far is extrapolated over the remaining days;
2. **future recurring expenses** — recognized subscriptions and recurring bills that are still expected before month-end are added explicitly.

Recurring transactions already paid during the month are removed from the run-rate before extrapolation, so a mortgage, subscription or other fixed bill is not counted twice. The budget report exposes the components separately (`recurring_actual`, `variable_actual`, `variable_remaining_projection`, `recurring_future`) for auditability and UI explanations.

The recurrence calendar is also calendar-aware: monthly, bimonthly, quarterly, semiannual and annual patterns advance by calendar months rather than fixed 30/60/90-day approximations. This prevents date drift such as `1 August -> 31 August` for a monthly subscription. Weekly and biweekly patterns remain day-based.

For the current month the dashboard shows both **Ricorrenze ancora attese** and **Trend variabile residuo** next to the overall end-of-month projection. For completed months, projection still equals actual spend.

## Manual recurrence management (v0.7)

Automatic recurrence detection is now explicitly subordinate to human decisions. Every detected pattern can be managed from the dashboard without changing or deleting the underlying transactions.

Supported states:

- **confirmed** — the user confirms that the pattern is genuinely recurring. Confidence becomes effectively authoritative for forecasting;
- **paused** — the historical pattern remains visible as recurring, but future occurrences are temporarily removed from forecasts;
- **ended** — the historical pattern remains part of cost-structure analysis, while no future occurrence is forecast;
- **rejected** — the pattern is treated as a false positive and is removed from recurrence-based forecasts and recurring cost classification.

A confirmed pattern can optionally override the automatically inferred amount, next expected date, cadence and add a local note. These overrides are stored in SQLite in `recurrence_overrides`. The source ledger remains immutable: only the interpretation of the recurrence is changed.

The same manual decision is consumed by both:

1. `GET /api/analytics/forecast`;
2. the hybrid monthly budget projection.

This guarantees that the dashboard does not show one recurrence value while the budget engine uses another.

New endpoints:

- `GET /api/recurrences/overrides`
- `PUT /api/recurrences/override`
- `DELETE /api/recurrences/override?pattern_key=...`

The dashboard provides a **Gestisci** action for every detected recurrence, including a reset command that removes the manual override and returns the pattern to automatic detection.

You can also add a **manual recurrence** without waiting for three historical transactions: set merchant, category, amount, cadence and next expected date. It is used immediately by the forecast and monthly budget projection.

During **Human-check**, each imported expense can also be marked **Ricorrente: Sì/No** before approving it. When set to **Sì**, choose its frequency; the same setting remains editable in the correction form if the row is marked **No**. At final import the app creates a local manual recurrence, immediately available to forecast and monthly-budget calculations.

The first Human-check step also allows direct edits to date, description, amount and category, plus recurring and suspicious flags. Pressing **Sì** after changing a detail stores it as a manual correction without sending the row to the later correction screen. The local classifier then learns the manually chosen category from the corrected merchant/description; amounts and dates are saved exactly as entered but are not predicted by the classifier.

Human-check navigation is reversible during the local review: use **Riga precedente** to revisit an earlier decision, and **Salta per ora** to postpone a rejected row while correcting other rows. Skipped rows remain in staging and must still be corrected before final import; nothing is discarded.

The **Modifica transazione** dialog offers the same **Spesa ricorrente** flag for an already recorded expense. Select a frequency to create or update its local manual recurrence; untick it and save to remove that manual recurrence again.

In Human-check corrections, **Segnala come spesa sospetta** adds the movement to the dashboard’s local suspicious-expenses queue. Suspicious movements are always included in totals, analytics and budget calculations; accepting one only clears the review flag.

## Local backup and export (v0.8)

The application can create a validated SQLite snapshot in `data/backups/` using `POST /api/backup`. The snapshot is produced with SQLite's backup API and checked with `PRAGMA integrity_check` before success is returned.

Use `POST /api/backup/validate` to verify that an uploaded SQLite backup is intact and contains the minimum Family Budget tables before trusting it. Exports remain local as well:

- `GET /api/export/csv` downloads the transaction ledger as UTF-8 CSV;
- `GET /api/export/xlsx` downloads a multi-sheet workbook containing transactions, accounts, categories, budgets, rules and recurrence overrides.

Backup and export files are ignored by Git. They must be stored only in a trusted location because they may contain financial data.

`POST /api/backup/restore` performs the same integrity and compatibility checks, creates a safety snapshot of the current ledger, then replaces the local database atomically. The dashboard exposes these actions under **Backup ed export locali**.
