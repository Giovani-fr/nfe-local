# nfe-local — Local Brazilian Electronic Invoice Viewer, Validator & Batch Analyzer (+ `NFeDistribuicaoDFe`)

> 🇧🇷 **Leia em Português:** [README.md](README.md)

A **100% local, deterministic, zero-mandatory-API-cost** tool for parsing, validating, batch-analyzing, rendering printable HTML mirrors (**DANFE** and **DANFSe**), and synchronizing inbound electronic invoices issued against a company's CNPJ via SEFAZ (**`NFeDistribuicaoDFe`**):
- **NF-e** (Model 55 — including early support for **Alphanumeric CNPJ under NT 2014.002 v1.40** and **Tax Reform IBS/CBS**).
- **NFC-e** (Model 65 — retail/consumer electronic invoice).
- **NFS-e** (exclusively the **National Standard** — `sped.fazenda.gov.br/nfse` and **DANFSe NT 008/2026**).

Your XML files and digital certificate never leave your infrastructure: all processing runs locally in Python (`lxml` hardened against XXE, `Pydantic v2` with exact `Decimal` arithmetic, in-memory `cryptography` PKCS#12, `SQLite`, and `FastAPI`).

![nfe-local Demo](docs/prints/demo.svg)

---

## Project Status

| Module / Feature | Status | How it was verified |
| :--- | :---: | :--- |
| **Anti-XXE Parser for NF-e (55), NFC-e (65), Summary & Events** | ✅ Ready | Automated test suite with synthetic XML fixtures (`nfeProc`, unsigned `NFe`, `resNFe`, `procEventoNFe`, and XXE attack blocking). |
| **National Standard NFS-e Parser (`infNFSe` / `infDPS` / `IBSCBS`)** | ✅ Ready | Verified with 50-digit access keys, ISSQN, federal withholdings (`IRRF`/`CSLL`/`CP`/`PIS`/`COFINS`), Tax Reform (`IBS/CBS`), and explicit rejection of legacy municipal layouts (ABRASF). |
| **Alphanumeric CNPJ & Access Key Validator (NT 2014.002 v1.40)** | ✅ Ready | Unit tests for Modulo 11 check digits via `ord(c) - 48` on 44-char keys and CNPJs containing letters (`12.ABC.345/01DE-35`). |
| **Financial Reconciliation ($\sum \text{items} == \text{vProd}$ & $\text{vNF}$ equation)** | ✅ Ready | Tested with balanced invoices and purposefully mismatched item/total fixtures using exact `Decimal` math. |
| **Streaming Batch Aggregator & CSV / 4-Sheet XLSX Export** | ✅ Ready | Real benchmark with **1,000 XMLs** (`0.63 MB` peak RAM in streaming) + protection against *CSV/Excel Formula Injection* and 44/50-char key preservation as text (`@`). |
| **Dark Mode Web UI + Printable A4 Mirrors (DANFE Code 128 & DANFSe)** | ✅ Ready | Integration tests via `TestClient` covering pure-SVG Code 128-C / 128-B barcodes and homologation watermarks. |
| **FastAPI REST API + Agent-Ready CLI** | ✅ Ready | REST endpoints (`/api/lote`, `/api/recebidas`, `/exportar`) and `nfe-local analisar` / `nfe-local sincronizar-sefaz` CLI commands tested end-to-end. |
| **Optional Analytical Assistant (LLM with SHA-256 Cache & Rate-Limit)** | ✅ Ready | Verified 100% offline with injected HTTP client, sending only aggregated accounting summaries (zero raw XML), disk cache, and hourly quota enforcement. |
| **Phase 2 — SEFAZ Inbound Sync (`NFeDistribuicaoDFe` + A1 Certificate + SQLite)** | ✅ Ready | Verified with ephemeral in-memory X.509/PKCS#12 certificates, SOAP 1.2 envelopes (`cStat 138`, `137`, and `656`), 1-hour lock against `656` penalties, and 180+ automated tests. |
| **Phase 3 — Persistent Batch History, Unified Archive & Visual A1 Certificate UI** | ✅ Ready | Uploaded batches persist in SQLite (`GET /api/lotes` & deduplicated `GET /api/acervo`), with in-browser `.pfx` upload, password validation, state (`cUFAutor`), and environment switching. |

---

## Key Features

1. **Multi-File `.xml` & `.zip` Upload (Phase 1):**
   - Processes dozens or thousands of individual XMLs or `.zip` archives in memory (with built-in *zip bomb* and *path traversal* protection).
   - Automatically detects NF-e (55), NFC-e (65), National NFS-e, Summary (`resNFe`), or Event (`procEventoNFe`).
2. **Fiscal, Financial & Batch Validation Engine:**
   - **Rule 1 (Access Key):** validates 44-character keys (numeric or alphanumeric) using Modulo 11 (`ord(c) - 48`) and 50-digit National NFS-e keys.
   - **Rule 2 (Party Documents):** validates CPF (11 digits) and CNPJ (14 numeric or alphanumeric characters per NT 2014.002 v1.40) for issuer and recipient.
   - **Rule 3 (Financial Reconciliation):** checks that item totals ($\sum \text{vProd}$) match the invoice header (`ICMSTot/vProd`) and verifies the `vNF` accounting equation.
   - **Rule 4 (Batch Duplicates):** flags duplicate access keys within the same batch and prevents double counting in financial summaries.
   - **Rule 5 (Operational Alerts):** highlights invoices issued in **Homologation/Testing (`tpAmb=2`)** or missing an **Authorization Protocol (`protNFe`)**.
3. **Printable A4 Mirrors (DANFE & DANFSe NT 008/2026):**
   - **DANFE (NF-e/NFC-e):** portrait A4 layout with detachable receipt stub, zero-dependency **pure-SVG Code 128 barcode** (automatically switching between numeric Subset C and alphanumeric Subset B), installments, tax breakdown, and line items.
   - **DANFSe (National NFS-e):** faithful to **NT 008/2026**, displaying the 50-digit key, Provider, Customer, Service (`cTribNac`/`cNBS`), ISSQN, Federal Withholdings, and Tax Reform (`IBS/CBS`).
4. **Executive Batch Summary & Accounting Export (CSV & XLSX):**
   - Aggregates totals by **Issuer/Provider**, by **Tax** (`ICMS`, `ICMS-ST`, `IPI`, `PIS`, `COFINS`, `ISS`, `Withholdings`, `IBS/CBS`), and by **Period** (`YYYY-MM`).
   - Exports UTF-8 with BOM CSVs and a **4-sheet XLSX workbook** (`Notas`, `Itens`, `Resumo`, `Alertas`).
5. **Inbound SEFAZ Invoice Synchronization (`NFeDistribuicaoDFe` — Phase 2):**
   - Performs SOAP 1.2 queries with in-memory mTLS using an ICP-Brasil A1 certificate (`.pfx`), decompresses `<docZip>` payloads (`base64 + gzip`), persists records in local SQLite, and provides the **"🏛️ Recebidas da SEFAZ (DistDFe)"** tab filterable by issuer, date range, amount, category, and status.

---

## Phase 2: Inbound Invoices (`NFeDistribuicaoDFe`) & Safe A1 Certificate Handling

### 1. Security of the ICP-Brasil e-CNPJ A1 Certificate (`.pfx`)
- **Visual Web UI Configuration or Environment Variables (`.env`):**
  - Directly from the web interface (**🏛️ Recebidas da SEFAZ** tab -> **"⚙️ Cadastrar / Trocar Certificado (.pfx)"**), you can upload your `.pfx`/`.p12` file, enter the password, and select the state (`cUFAutor`) and environment (`Homologation` or `Production`). The pair is validated in memory with `cryptography` before being stored under a fixed safe filename (`data/certificado/certificado.pfx`).
  - Alternatively, for headless servers or CLI/Docker automation, configure via `.env`:
  ```ini
  NFE_CERT_PATH=/secure/path/company_a1.pfx
  NFE_CERT_SENHA=your-pfx-password
  NFE_AMBIENTE=homologacao       # homologacao (2, default) or producao (1)
  NFE_UF_AUTOR=35                # IBGE 2-digit state code (e.g. 35 = SP)
  NFE_DB_PATH=data/nfe_recebidas.db
  ```
- **Zero unencrypted private keys on disk (in-RAM mTLS):**
  - `sefaz/cert_a1.py` and `sefaz/dist_dfe.py` load the `.pfx` (PKCS#12) bundle via `cryptography` and negotiate the mTLS handshake directly in RAM (`requests-pkcs12`). No temporary `.pem` or unencrypted `.key` files are ever written to disk.
  - The company's CNPJ is automatically extracted from the ICP-Brasil `SubjectAlternativeName` extension (**OID `2.16.76.1.3.3`**) or the `Common Name`, supporting both numeric and alphanumeric CNPJs.
- **Git & Docker Isolation:**
  - Both `.gitignore` and `.dockerignore` strictly exclude `*.pfx`, `*.p12`, `*.pem`, `*.key`, `.env`, `senha.txt`, and `data/*.db`.
  - When running in Docker, mount the `.pfx` file as a **read-only volume (`:ro`)** at runtime (never bake certificates into an image):
    ```bash
    docker run --rm -p 127.0.0.1:8765:8765 \
      -v /host/secure/cert.pfx:/run/secrets/cert.pfx:ro \
      -v $(pwd)/data:/app/data \
      -e NFE_CERT_PATH=/run/secrets/cert.pfx \
      -e NFE_CERT_SENHA="your-password" \
      -e NFE_AMBIENTE=homologacao \
      nfe-local:latest
    ```
- **100% Synthetic Test Suite:**
  - The automated test suite (`pytest`) generates ephemeral self-signed X.509/PKCS#12 certificates in memory and uses synthetic SOAP envelopes (`tests/fixtures/sefaz/`), with zero real certificates or tax data in the repository.

### 2. SEFAZ Operational Rules Implemented
- **Incremental NSU Synchronization (`ultNSU` / `maxNSU`):**
  - The 15-digit cursor is persisted in `dfe_cursor` after each page (`cStat 138`). Once `ultNSU == maxNSU` or SEFAZ returns `cStat 137` (*No documents found*), pagination stops immediately without extra requests.
- **Strict Protection Against `cStat 656` Lockouts (Improper Consumption):**
  - If SEFAZ returns `cStat 656`, the engine syncs the `ultNSU` reported by SEFAZ and writes a mandatory **1-hour (3,600 s)** cooldown lock (`bloqueado_ate`) into SQLite.
  - Any sync attempt before the lock expires is rejected locally (HTTP `429` in the API / `ErroBloqueioSEFAZ` in the CLI) **without touching the network**, preventing the 1-hour penalty timer from resetting on SEFAZ servers.
- **Summary (`resNFe`) vs. Full Invoice (`procNFe`) & Cancellation Events (`110111`/`110112`):**
  - Invoices initially delivered as `<resNFe>` summaries are flagged with the **`⚠️ Exige Manifestação (210210)`** badge, indicating that *Recipient Manifestation* (e.g., Operation Awareness `210210`) is required to unlock the full `<procNFe>` XML.
  - When the full `<procNFe>` arrives in a later NSU, the SQLite record is **automatically upgraded** to `NFE_COMPLETA` (with anti-downgrade protection if an older summary is reprocessed). Cancellation events (`110111` / `110112`) automatically mark the corresponding invoice as `CANCELADA`.
- **SEFAZ ~90-Day Retention Window:**
  - The national `NFeDistribuicaoDFe` WebService only retains documents for roughly 90 days. Periodic synchronization and regular backups of `data/nfe_recebidas.db` are recommended.

---

## Measured Speed & Memory (1,000-XML Benchmark)

Real measurement executed via [`scripts/benchmark_lote.py`](scripts/benchmark_lote.py) on **Windows 11 (Python 3.12.10, Intel64)** across a mixed batch of **1,000 synthetic XMLs** (250 numeric NF-e 55, 250 NFC-e 65, 250 alphanumeric-CNPJ NF-e 55, and 250 National NFS-e):

| Measured Scenario | Total Time | Throughput | Peak Memory (`tracemalloc`) | Output |
| :--- | :---: | :---: | :---: | :--- |
| **Streaming Mode (`iterar_lote` + `gerar_resumo_lote`)** | **5.92 s** | **169.1 XMLs/s** | **0.63 MB** | Keeps only 1 DOM tree in memory at a time |
| **Full Batch + 4-Sheet XLSX Export (`openpyxl`)** | **14.56 s** | **68.7 XMLs/s** | **32.44 MB** | Generates a complete **275.6 KB** `.xlsx` workbook |

Full methodology and reproduction instructions are available in [`docs/benchmark.md`](docs/benchmark.md).

---

## Installation & Usage

### Option 1: One-Click Windows Installer (Recommended)

1. Double-click **`instalar.bat`**.
   - Checks for Python 3.12, creates an isolated virtual environment outside OneDrive (`%USERPROFILE%\venvs\nfe-local`), installs dependencies, and creates a **"Visualizador NF-e Local"** shortcut on your Desktop.
2. Double-click the Desktop shortcut or **`iniciar.bat`**.
   - Your browser will open `http://127.0.0.1:8765`.
   - Use **"Carregar Lote de Demonstração"** on the Batch Analyzer tab, or switch to **"🏛️ Recebidas da SEFAZ (DistDFe)"** and click **"🧪 Carregar Recebidas de Demonstração"** to test the inbound filters without a real A1 certificate.

### Option 2: Command-Line Interface (CLI)

```powershell
# Print batch summary in terminal and export a 4-sheet XLSX report from a folder
nfe-local analisar .\invoices\ --exportar-xlsx fiscal_report.xlsx

# Structured JSON output for scripts or AI agents
nfe-local analisar invoice1.xml batch.zip --json

# Run local simulated SEFAZ DistDFe synchronization with JSON output
nfe-local sincronizar-sefaz --demo --json

# Synchronize inbound invoices from SEFAZ (requires NFE_CERT_PATH and NFE_CERT_SENHA)
nfe-local sincronizar-sefaz --ambiente 2 --db data/nfe_recebidas.db

# Start the local web server manually
nfe-local servidor --host 127.0.0.1 --port 8765
```

### Option 3: Manual Python Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.app:app --host 127.0.0.1 --port 8765
```

### Option 4: Docker

```bash
docker build -t nfe-local:latest .
docker run --rm -p 127.0.0.1:8765:8765 nfe-local:latest
```

---

## How It Works Under the Hood

```mermaid
flowchart LR
    A["Local Input (.xml / .zip)"] --> B["Safe Extractor (Anti-ZipBomb)"]
    S["SEFAZ NFeDistribuicaoDFe (SOAP 1.2 mTLS)"] --> Z["docZip Decompressor (Base64 + GZIP)"]
    Z --> C["Anti-XXE lxml Parser (resolve_entities=False)"]
    B --> C
    C --> D["Auto Detector (NF-e 55 / NFC-e 65 / NFS-e / Summary / Event)"]
    D --> E["Pydantic v2 Models (Exact Decimal Values)"]
    E --> F["Validation Engine (Key, Alphanumeric CNPJ, vProd/vNF Sum, Batch)"]
    F --> DB["Local SQLite (dfe_cursor / notas_recebidas / sync_log)"]
    F --> G["O(1) Streaming Aggregator (ResumoLote)"]
    F --> H["Printable A4 Mirrors (DANFE Code 128 SVG / DANFSe NT 008/2026)"]
    G --> I["CSV (BOM) & 4-Sheet XLSX Exporter (Anti-Formula-Injection)"]
```

---

## Known Limitations

1. **Legal Validity of HTML Mirrors (DANFE / DANFSe):**
   - The printable HTML mirrors are auxiliary visual representations for human review and auditing. The legally binding tax document is always the **digitally signed original XML file**.
2. **NFS-e Scope Restricted to the National Standard:**
   - For service invoices (NFS-e), only the **National Standard** (`http://www.sped.fazenda.gov.br/nfse`) is supported. Legacy municipal layouts (such as ABRASF) are explicitly rejected with a clear message, as they are being phased out nationwide.
3. **A3 Hardware Certificates (USB Token / Smartcard) Not Supported:**
   - The `NFeDistribuicaoDFe` module exclusively supports **A1 Digital Certificates (`.pfx` / PKCS#12)** loaded in memory. Hardware-based PKCS#11 A3 tokens are out of scope.

---

## How I used AI (Engineering Log & Human Review)

This project was built incrementally (one task at a time from [`docs/BACKLOG.md`](docs/BACKLOG.md), enforcing strict TDD with `pytest` and `ruff`). Below are the critical areas where naive AI suggestions could have introduced fiscal bugs or security flaws, and how they were caught and fixed:

| Task / Module | What was asked of AI | Pitfall / Risk caught during review | Fix applied & verification |
| :--- | :--- | :--- | :--- |
| **1–2. Synthetic Fixtures & Privacy** | Generate XML fixtures covering NF-e 55, NFC-e 65, National NFS-e, alphanumeric CNPJ, and fiscal defects. | Risk of invalid check digits or accidental real company data. | Built deterministic Modulo 11 generators and privacy audit tests (`test_fixtures.py`) guaranteeing 100% synthetic data. |
| **3. Normalizers & Alphanumeric CNPJ** | Sanitize CNPJ and Access Keys for Pydantic v2 models. | Standard regex `re.sub(r"\D", "", doc)` strips uppercase letters `[A-Z]` from the new Alphanumeric CNPJ (NT 2014.002 v1.40). | Implemented alphanumeric cleaning (`[^0-9A-Za-z]`), safe prefix stripping (`NFe`, `NFS`, `DPS`), and strict `Decimal` conversion. |
| **4. Anti-XXE `lxml` Parser** | Namespace-agnostic parser for NF-e/NFC-e with external entity blocking. | 1) `lxml` raises `FutureWarning` on truthiness checks (`if elem:`); 2) CT-e documents can contain nested `<infNFe>` tags without `<ide>`. | Replaced truthiness checks with `if elem is not None:`, required a direct `<ide>` child on `<infNFe>`, and disabled `resolve_entities` and `load_dtd`. |
| **5. Validation Engine** | Alphanumeric Modulo 11 rules, financial reconciliation, and batch duplicate detection. | Cancellation events (`procEventoNFe`) reference the same `chNFe` as the original invoice and would trigger false duplicate errors. | Scoped `CHAVE_DUPLICADA_LOTE` to primary documents (`NFE`, `NFCE`, `NFSE`, `RESUMO`) and verified the ASCII `ord(c) - 48` formula. |
| **6–7. Detector, ZIP & Streaming Summary** | In-memory `.zip` extraction and batch aggregator for 1,000+ XMLs. | Loading all DOM trees from a large `.zip` simultaneously would exhaust RAM or allow zip bombs. | Added strict decompression quotas and built `AcumuladorResumoLote` in streaming mode ($O(1)$ DOM trees, using just `0.63 MB` for 1,000 XMLs). |
| **8. CSV & XLSX Export** | Generate Excel-ready reports in pt-BR. | 1) Excel converts 44/50-digit numeric keys into scientific notation floats (`3.526E+43`), corrupting trailing digits; 2) Fields starting with `=+-@` enable *Formula Injection*. | Forced `data_type = "s"` and `number_format = "@"` on key/ID columns in `openpyxl` and added `sanitizar_celula_texto` prefixing `'`. |
| **9–10. API, Web UI & Code 128 SVG** | FastAPI endpoints, Dark Mode UI, and 44-char access key SVG barcode. | **Code 128 Subset C** only encodes digit pairs (`00`–`99`) and breaks when the access key contains an alphanumeric CNPJ. | Implemented automatic switching in `barcode_svg`: **Subset C** for 44 numeric digits and **Subset B** for 44 alphanumeric characters. |
| **11. Infra & Benchmark** | Non-root `Dockerfile`, Windows `.bat` scripts, and 1,000-XML benchmark. | `.bat` files with UTF-8 accented characters break on Windows `cmd.exe` (`CP850`/`CP1252`). | Audited `instalar.bat` and `iniciar.bat` as pure ASCII (`test_infra_benchmark.py`) and measured `0.63 MB` peak RAM with `tracemalloc`. |
| **12. Optional LLM Assistant** | Batch Q&A endpoint with disk cache and hourly rate limit. | Sending raw XMLs would leak sensitive data/waste tokens, and counting rate limits before checking the cache would penalize repeated queries. | Restricted the prompt context to the deterministic `ResumoLote` and checked the `SHA-256` cache before incrementing the hourly rate-limit counter. |
| **13. A1 Certificate & SOAP 1.2** | In-memory PKCS#12 reader and `NFeDistribuicaoDFe` SOAP 1.2 client. | Raw `OtherName` bytes for ICP-Brasil OID `2.16.76.1.3.3` include the ASN.1 DER header (`tag + length`), which in a 14-byte `UTF8String` (`0x0c 0x0e`) leaks into alphanumeric cleaning if stripped by regex alone. | Decoded the ASN.1 DER header prior to document sanitization and used `Pkcs12Adapter` for in-RAM mTLS without writing `.pem` files to disk. |
| **14–15. NSU Control & `docZip`** | `138`/`137`/`656` state machine and `<docZip>` decompressor. | Firing an extra query after reaching `ultNSU == maxNSU` on `cStat 138` triggers SEFAZ `cStat 656` (Improper Consumption). | Stopped the pagination loop immediately when `ultNSU >= maxNSU`, synced the `ultNSU` returned inside `656` rejections, and capped GZIP reads at `10 MB` against *gzip bombs*. |
| **16–18. SQLite, Inbound UI & E2E Tests** | SQLite persistence, filterable Web tab, and simulated E2E suite (`138`, `137`, `656`). | A delayed `resNFe` summary could overwrite an existing full `procNFe`, or repeated UI clicks could reset SEFAZ's 1-hour lock timer. | Enforced anti-downgrade rules (`NFE_COMPLETA` is never overwritten by `RESUMO_NFE`), automatic cancellation updates (`110111`/`110112`), local HTTP `429` blocking during `656` windows, and 100% offline E2E verification. |

---

## Running Tests & Linter

```powershell
ruff check .
ruff format --check .
pytest -v
```

---

## Third-Party Licenses & Project License

Released under the **MIT License** (see [`LICENSE`](LICENSE)). Built on open-source libraries (`FastAPI`, `Uvicorn`, `Pydantic`, `lxml`, `openpyxl`, `cryptography`, `requests`, and `requests-pkcs12`).
