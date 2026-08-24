# GuardianLens Data Collector v_1.0.0

Manual marketplace listing capture, AI-assisted field extraction, privacy processing, duplicate checks, product-image storage, human review, and approved-dataset export for GuardianLens FYP2.

This package implements **data collection only**. It intentionally does not contain fraud labels, train/validation/test splitting, model training, fraud scoring, calibrated AI confidence, or an unattended marketplace crawler.

## 1. Core rule

The collection boundary is deliberate:

```text
YOU manually choose a listing
        ↓
YOU open the listing in Chrome
        ↓
YOU click GuardianLens Capture
        ↓
local staging
        ↓
batch processor
        ↓
AI screenshot extraction (OpenAI, Anthropic, OpenRouter, or Gemini — set by AI_PROVIDER)
        ↓
PII scrub + validation + duplicate/image checks
        ↓
ready_for_review / needs_attention
        ↓
YOU review and correct
        ↓
approve / reject / skip / flag
        ↓
approved dataset
```

The extension never searches marketplaces, follows pagination, opens seller pages, discovers URLs, or chooses products for you.

## 2. Dataset target

Production target:

- Total approved listings: **2,500**
- Carousell Malaysia: **1,500 (60%)**
- Mudah.my: **1,000 (40%)**
- Only `approved` records count.
- Captured, processing, waiting-for-review, skipped, flagged, rejected, and failed records do not count toward 2,500.

Category targets are configurable in `config/categories.yaml`.

## 3. What is included

```text
GuardianLens_Data_Collector_v2.0.0/
├── main.py                         # production server entry point
├── environment.yml                # Conda environment
├── requirements.txt               # pip mirror of runtime/test dependencies
├── .env.example                   # secret/config template
├── setup.bat                      # Windows Conda setup
├── run.bat                        # Windows production run
├── config/
│   ├── categories.yaml            # configurable category mix
│   └── platform_targets.yaml      # 2500 / 1500 / 1000 targets
├── extension/
│   ├── manifest.json              # Chrome Manifest V3
│   ├── background.js              # manual current-tab capture
│   ├── popup.html
│   └── popup.js
├── src/guardianlens/
│   ├── app.py                     # FastAPI app and dashboard/review routes
│   ├── batch.py                   # start/pause/resume/stop + recovery
│   ├── capture.py                 # manual-capture staging boundary
│   ├── config.py
│   ├── db.py
│   ├── schema.sql
│   ├── states.py                  # listing state machine
│   ├── openai_extractor.py        # OpenAI screenshot extraction adapter
│   ├── anthropic_extractor.py     # Anthropic screenshot extraction adapter
│   ├── openrouter_extractor.py    # OpenRouter (free vision models) adapter
│   ├── gemini_extractor.py        # Gemini screenshot extraction adapter
│   ├── extraction_contract.py     # shared schema/prompt rules for all providers
│   ├── provider_info.py           # per-provider model listing
│   ├── errors.py                  # provider error classification (auth/quota/other)
│   ├── models.py                  # request/response Pydantic models
│   ├── security.py                # local collector token
│   ├── release.py                 # release-zip secret exclusion
│   ├── pii.py                     # PII scrub + residual checks
│   ├── images.py                  # validation, hashes, storage
│   ├── dedupe.py                  # URL/metadata/image duplicate helpers
│   ├── planner.py                 # platform/category recommendations
│   ├── processor.py               # post-capture processing pipeline
│   ├── review.py                  # human edit/actions + atomic approval
│   ├── exports.py                 # approved-only export
│   ├── audit.py                   # security/data-integrity audit
│   ├── templates/                 # local dashboard and review UI
│   └── static/
├── scripts/
│   ├── init_db.py
│   ├── self_test.py
│   ├── run_tests.py
│   ├── run_soft_test_server.py
│   ├── reset_soft_test.py
│   ├── mock_integration_check.py
│   ├── security_audit.py
│   ├── data_integrity_audit.py
│   ├── export_approved.py
│   ├── manifest.py
│   ├── build_release_zip.py
│   └── verify_fresh_release.py
├── data/
│   ├── private/
│   │   ├── screenshots/           # temporary/private evidence
│   │   └── staging/               # private staged capture info
│   ├── images/
│   │   ├── carousell/             # long-term dataset product images
│   │   └── mudah/
│   ├── temporary/
│   ├── exports/
│   ├── reports/
│   └── soft_test/                 # isolated soft-test DB + runtime assets
├── tests/
├── docs/
└── logs/
```

Empty runtime folders contain `.gitkeep` so the structure survives ZIP extraction and Git.

---

# 4. Required software

Recommended Windows setup:

1. Windows 11
2. Miniconda or Anaconda
3. Google Chrome
4. Internet connection for AI processing and remote product-image download
5. An API key for at least one of: OpenAI, Anthropic, OpenRouter, Gemini. Pick the active one with `AI_PROVIDER` in `.env`; you can also switch it live from the dashboard.
6. Node.js is optional. It is used only by `scripts/self_test.py` to syntax-check extension JavaScript if installed.

Python is installed inside the Conda environment. You do not need to depend on your global Python installation.

---

# 5. First-time setup with Conda

Open **Anaconda Prompt** or a terminal where `conda` works.

```bat
cd path\to\GuardianLens_Data_Collector_v2.0.0
setup.bat
```

The setup script does this:

```text
conda env update -f environment.yml --prune
copy .env.example .env      (only if .env does not already exist)
initialize SQLite
create/verify local collector token
run structural self-test
```

From PowerShell, run the same batch file as `.\setup.bat`. It does not require a
PowerShell execution-policy change.

## Manual Conda commands

If you prefer to see each command:

```powershell
conda env create -f environment.yml
conda activate guardianlens-collector
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python scripts/init_db.py
python scripts/self_test.py
python scripts/run_tests.py
```

If the environment already exists:

```powershell
conda env update -f environment.yml --prune
conda activate guardianlens-collector
```

---

# 6. Configure `.env`

Open `.env`, set `AI_PROVIDER` to the one you're using, and fill in only that provider's key
(the other three can stay blank):

```env
AI_PROVIDER=openai
OPENAI_API_KEY=your_real_key_here
OPENAI_MODEL=gpt-5.6-luna
OPENAI_PROMPT_VERSION=collector_v1
HOST=127.0.0.1
PORT=8765
OPERATOR_TIMEZONE=Asia/Kuala_Lumpur
SCREENSHOT_RETENTION=review_only
MAX_PRODUCT_IMAGES=12
MAX_IMAGE_BYTES=12582912
IMAGE_MIN_WIDTH=160
IMAGE_MIN_HEIGHT=160
REQUEST_TIMEOUT_SECONDS=45
GUARDIANLENS_MODE=production
```

The `*_MODEL` defaults in `.env.example` are placeholders, not guaranteed-valid API model IDs —
verify (or change) them from the dashboard's **AI provider → View models for active provider**
before running a real batch, so the whole queue doesn't fail on a bad model name.

Rules:

- Keep every provider API key only in `.env`.
- Never paste any of them into the Chrome extension.
- Never commit `.env` to Git.
- Keep `HOST=127.0.0.1`.
- Set `OPERATOR_TIMEZONE` to the operator's IANA time-zone name. The supplied Malaysian default is `Asia/Kuala_Lumpur`.
- The server intentionally refuses `0.0.0.0` by default.
- The extension receives only the separate local collector token.

Every provider model and prompt version is configurable, so the collector is not tied to
one provider or model name.

---

# 7. Run the production collector

## Windows (CMD, Anaconda Prompt, or PowerShell)

```bat
run.bat
```

From PowerShell, use `.\run.bat`.

## Activated Conda environment

```powershell
conda activate guardianlens-collector
python main.py
```

Then open:

```text
http://127.0.0.1:8765
```

Health endpoint:

```text
http://127.0.0.1:8765/health
```

The dashboard shows:

- approved total / 2,500
- Carousell / 1,500
- Mudah / 1,000
- exact counts for every lifecycle/failure state
- pending/processing/attention and review queues with safe failure reasons
- searchable, paginated **All Records** history (including rejected, skipped, failed, and duplicates)
- category coverage
- collection recommendations
- batch controls
- local extension token
- review timing in `OPERATOR_TIMEZONE`
- recent audited activity

---

# 8. Install the Chrome extension

The extension is loaded locally. It is not published to the Chrome Web Store.

1. Start GuardianLens first.
2. Open Chrome.
3. Go to `chrome://extensions`.
4. Enable **Developer mode**.
5. Click **Load unpacked**.
6. Select the project folder:

```text
GuardianLens_Data_Collector_v2.0.0\extension
```

7. Pin **GuardianLens Manual Collection** to the toolbar.
8. Open `http://127.0.0.1:8765`.
9. Copy the **Chrome extension token** shown on the dashboard.
10. Click the GuardianLens extension.
11. Keep the server URL as:

```text
http://127.0.0.1:8765
```

12. Paste the local token.
13. Click **Save settings**.

The token is stored in Chrome extension local storage. It is separate from every provider
API key.

---

# 9. What the extension actually captures

Only after **you manually click Capture Current Listing**, it captures the active tab:

- current URL
- detected platform
- page title
- one visible-tab screenshot
- up to 12 large visible product-image candidate URLs
- marketplace listing ID when a reliable numeric ID is visible in the URL
- capture timestamp on the backend

Before the request is accepted, the extension and backend both require an exact supported
marketplace host, an individual-listing URL shape, and listing-specific page evidence
(for example, a product heading plus price/product signals). Home, category, search,
seller-profile, malformed, deceptive-host, and tab-changed-during-capture requests are
rejected without creating a record.

Raw page text is **not persisted**. This avoids retaining seller names/usernames or contact text that is difficult to reliably scrub. The private screenshot is used for AI extraction and human verification.

The extension does **not**:

- open marketplace pages for you
- search products
- click pagination
- iterate seller profiles
- follow listing links
- discover URLs
- run unattended collection

---

# 10. Required two-listing soft test

Do this before real collection.

The soft test uses a separate database **and** a separate runtime asset tree:

```text
data/soft_test/collector_soft_test.sqlite3
data/soft_test/runtime/private/screenshots/
data/soft_test/runtime/images/
data/soft_test/runtime/exports/
```

It does not count toward the production 2,500 and its screenshots/images do not enter the production asset folders.

## Start soft-test mode

Stop the production server first, then run:

```powershell
conda activate guardianlens-collector
python scripts/run_soft_test_server.py
```

The dashboard still uses:

```text
http://127.0.0.1:8765
```

## Capture exactly two real trial listings

Capture:

1. One manually selected Carousell Malaysia listing
2. One manually selected Mudah.my listing

For each listing:

```text
manually open listing
→ extension Capture Current Listing
→ verify it appears as captured
```

## Process them

Dashboard → Batch processor:

```text
Minutes: 30
Max records: 2
Start
```

Expected flow:

```text
captured
→ processing
→ ai_extracted
→ validating
→ ready_for_review OR needs_attention
```

If something fails, the record should enter a named failure/attention state instead of silently disappearing.

## Human review

Open **Review Queue**.

For each of the two records verify:

- title
- description
- category
- condition
- price
- state/city
- account age if visible
- seller rating if visible
- review count if visible
- active listing count if visible
- language
- private screenshot
- product images
- warnings
- AI provider/model/prompt provenance
- complete status and review history
- duplicate checks/warnings and image-integrity results
- a human edit is retained in the correction audit

Correct mistakes, save, then approve only when correct.

Approval requires at least one stored product image and the final integrity checks to pass.

After both approvals, use a second PowerShell window to verify the isolated soft-test
dataset and produce its approved-only export:

```powershell
$env:GUARDIANLENS_MODE = 'soft_test'
python scripts/security_audit.py
python scripts/data_integrity_audit.py
python scripts/export_approved.py
```

Confirm that both audits pass, the approved count increases by two, the planner updates,
and the export is written only under `data/soft_test/runtime/exports/`. Do not begin a
larger collection campaign unless both real listings complete this checklist.

`GUARDIANLENS_MODE` remains set in that PowerShell window. Close it or run
`Remove-Item Env:GUARDIANLENS_MODE` before launching production. The collector token and
dashboard-selected provider override are intentionally shared between production and
soft-test modes; the databases and all collection assets/outputs are isolated.

## Reset soft test when needed

Stop the soft-test server first. This command deliberately deletes only the isolated
soft-test database and runtime asset/output tree; it never deletes production data.

```powershell
python scripts/reset_soft_test.py
```

Then restart `scripts/run_soft_test_server.py`.

---

# 11. Automated engineering integration check

This is different from the university soft test. It uses generated images and a deterministic mock extractor to verify the internal pipeline without spending API credits or using marketplace data.

```powershell
conda activate guardianlens-collector
python scripts/mock_integration_check.py
```

Expected:

```text
MOCK INTEGRATION CHECK: PASS
```

It creates exactly two synthetic records inside a disposable OS temporary directory and
checks capture → processing → review → approval. It does not write the production or
persistent soft-test database/assets.

Synthetic test records are not research data.

---

# 12. Daily collection workflow

## A. Check what is under target

Open the dashboard.

Example:

```text
What should I collect next?
Mudah → Cameras
Carousell → Sports
Mudah → Gaming
```

The planner uses approved plus pending records to avoid over-collecting one category while a large review queue is waiting.

## B. Manually select a listing

Example:

```text
Dashboard says: Mudah → Cameras
↓
YOU open Mudah
↓
YOU manually search Cameras
↓
YOU choose one listing
↓
YOU open it
```

## C. Capture

Click:

```text
GuardianLens → Capture Current Listing
```

The request is accepted only for Carousell Malaysia or Mudah hosts and only with the local collector token.

## D. Repeat

You can manually capture 20, 30, or 50 pages without waiting for AI processing after each one.

They remain in `captured` status.

## E. Process pending captures

Use the dashboard batch controls.

Example:

```text
Duration: 60 minutes
Maximum records: 50
Start
```

Processing stops when either limit is reached, the queue is empty, or you request Stop.

## F. Review

Open the review queue.

Human actions are:

```text
Approve
Edit + Approve
Reject
Skip
Flag
```

`approved` means accepted collection data. It does not mean legitimate or fraudulent.

---

# 13. Batch controls

Supported controls:

- Start
- Pause
- Resume
- Stop

Stop is graceful at the queue level. The worker finishes or fails the current listing, then stops before starting another one.

The latest run stores:

- run ID
- start/end times
- duration limit
- item limit
- attempted/processed
- successful
- duplicates
- AI failures
- image failures
- failed count
- run status

Crash recovery runs on server startup:

- listings left in `processing` are returned to `captured`
- listings left in later intermediate processing states become visible `processing_failed` records
- interrupted review sessions are closed as `review_session_interrupted` and returned to their prior reviewable state; offline time is not counted as human review time
- unfinished batch runs become `interrupted`
- processor control returns to `idle`

---

# 14. Listing state machine

Normal path:

```text
captured
→ processing
→ ai_extracted
→ validating
→ ready_for_review / needs_attention
→ under_review
→ approved / rejected / skipped / flagged
```

Failure states:

```text
capture_failed
processing_failed
ai_failed
image_failed
duplicate_blocked
```

New batches select only `captured` rows. Failed rows remain visible in their failure state and are not retried forever; retry one only after the underlying problem is corrected and it is explicitly returned to the capture queue.

Every state transition is recorded in `status_history`.

---

# 15. AI role and provenance

AI is only an automatic form filler during this phase.

Input:

```text
private listing screenshot
+ platform
+ page title
+ configured category list
```

Output fields:

```text
title
description
category
condition
price
currency
location_state
location_city
account_age_days
seller_rating
review_count
active_listing_count
language
```

The model is instructed not to return seller identity, email, phone, bank details, contact handles, or exact street addresses.

There are deliberately **no AI confidence fields**.

Provenance retained:

```text
ai_extracted
ai_provider
ai_model
ai_prompt_version
ai extraction timestamp
structured extraction result
status/error
```

Human edits are written to `review_actions` with previous and updated values.

---

# 16. Screenshot privacy

Private screenshots are stored separately:

```text
data/private/screenshots/<platform>/<listing_id>/listing.jpg
```

They are never included in normal dataset exports.

Default:

```env
SCREENSHOT_RETENTION=review_only
```

Supported values:

- `review_only`: delete screenshot after approval or rejection
- `retain_private`: keep screenshots privately
- `delete_after_approval`: delete only after approval

Private staging material, including the staged image-URL JSON, is removed after final approval/rejection.

---

# 17. Product images

Product images are dataset assets:

```text
data/images/carousell/<listing_id>/image_001_<unique-id>.jpg
data/images/mudah/<listing_id>/image_001_<unique-id>.jpg
```

Checks before storage:

```text
file exists/download succeeds
→ HTTPS-only public host and redirect validation
→ content type is image
→ supported format
→ maximum size
→ maximum decoded pixel count
→ minimum dimensions
→ image decode/verify
→ safe re-encode with EXIF/ancillary metadata removed
→ SHA-256 of the stored bytes
→ perceptual hash
→ exact duplicate check
→ near-duplicate warning
→ atomic file move
```

Supported formats:

- JPEG
- PNG
- WebP

Image metadata stored:

- image ID
- listing ID
- platform
- storage path
- SHA-256
- perceptual hash
- file size
- width/height
- source position
- primary flag
- source image URL without query parameters or fragments
- created timestamp

---

# 18. PII processing

The collection pipeline removes or flags patterns including:

- email addresses
- Malaysian phone numbers
- generic international phone-like numbers during residual checks
- obvious bank-account phrases
- contact-handle phrases such as WhatsApp/Telegram
- obvious exact street-address patterns

The collector keeps research-relevant seller behaviour such as account age, rating, review count, active listing count, and broad state/city.

Human review is still mandatory. Regex privacy checks are guardrails, not a replacement for checking the screenshot and extracted draft.

---

# 19. Duplicate handling

## Source duplicate

Uses canonical URL SHA-256.

Tracking query parameters and fragments are removed before hashing.

## Marketplace listing ID duplicate

Unique per platform when an ID is available.

## Metadata duplicate

Signature:

```text
platform
+ normalized title
+ normalized description
+ price
```

An exact match against an approved record blocks the duplicate draft/approval path.

## Exact image duplicate

Uses SHA-256.

## Near image duplicate

Uses a compact perceptual hash and Hamming distance.

Near duplicates create a warning for human review. They are not automatically rejected.

---

# 20. Atomic approval

Approval uses an SQLite `BEGIN IMMEDIATE` transaction.

Before commit GuardianLens checks:

1. current status is reviewable
2. title/category/price/currency exist
3. category is configured
4. at least one product image exists
5. residual PII check passes
6. source URL is not already approved
7. marketplace listing ID is not already approved
8. metadata signature is not already approved
9. approved timestamp and state history can be written together

If a check fails, the transaction rolls back.

There is no partial approval.

Screenshot/staging retention cleanup occurs after the database outcome commits because
SQLite cannot transactionally delete filesystem objects. If cleanup fails, GuardianLens
keeps the approved/rejected outcome, records and displays a cleanup warning, returns an
error to the operator, and makes the integrity audit fail until the private artifact is
resolved. It never silently reports cleanup success or rolls back only half of an approval.

---

# 21. SQLite database

Production DB:

```text
data/collector.sqlite3
```

Soft-test DB:

```text
data/soft_test/collector_soft_test.sqlite3
```

Core tables:

- `listings`
- `images`
- `ai_extractions`
- `review_actions`
- `warnings`
- `status_history`
- `processing_runs`
- `processor_control`

SQLite uses foreign keys, WAL mode, and a busy timeout.

The schema is versioned. Before a legacy database is migrated, GuardianLens creates and
validates an online SQLite backup under:

```text
data/backups/schema_migrations/
```

Migration runs under a write lock, re-checks the durable schema version, preserves user
rows, and validates the schema, foreign keys, indexes, migration history, and
`PRAGMA quick_check` before commit.

No external database server is required for this collection phase.

---

# 22. Export approved data

Dashboard export endpoint exists, or run:

```powershell
conda activate guardianlens-collector
python scripts/export_approved.py
```

Files are created in:

```text
data/exports/
```

Outputs:

```text
approved_listings_<timestamp>.csv
approved_images_<timestamp>.jsonl
```

The listing export intentionally excludes:

- screenshots
- screenshot paths
- private staging material/path
- provider API keys
- local collector token
- AI confidence fields
- raw seller identity/contact details

---

# 23. Run all automated tests

```powershell
conda activate guardianlens-collector
python scripts/run_tests.py
```

or:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=.tmp\pytest-manual
```

The automated suite covers core behaviour including:

- PII email removal
- phone removal
- simple false-positive check
- URL canonicalization
- URL duplicate detection
- metadata signatures
- image corruption/minimum-size checks
- exact image duplicate detection
- state transition enforcement
- private staging
- wrong-platform URL rejection
- mock AI extraction
- invalid AI shape rejection
- missing-image attention path
- human edit auditing
- PII scrub on human edits
- skip/flag/reject transitions
- atomic approval requirements
- screenshot cleanup
- planner 2,500 / 1,500 / 1,000 targets
- approved-only export field protection
- local-token creation
- non-local bind rejection
- security audit
- data-integrity audit baseline
- local API token enforcement
- batch start/pause/resume/stop
- crash recovery

---

# 24. Structural self-test

Run:

```powershell
python scripts/self_test.py
```

It checks:

- required folder structure
- category percentages sum to 100
- target total is 2,500
- Carousell target is 1,500
- Mudah target is 1,000
- Manifest V3 extension
- no `<all_urls>` extension permission
- JavaScript syntax when Node.js is available
- localhost-only bind configuration
- SQLite schema creation/readability in an OS temporary directory
- local token entropy without creating or changing the production token

The self-test does not initialize the production database or write runtime files into the project.

## Release-package verification

After all source changes are final, regenerate and verify the source manifest, build the strict-allowlist ZIP, then test a fresh temporary extraction:

```powershell
python scripts/manifest.py --generate
python scripts/manifest.py --verify
python scripts/build_release_zip.py
python scripts/verify_fresh_release.py
```

The release ZIP excludes databases and sidecars, logs, private screenshots/staging, captured product images, tokens, `.env`, caches, dependencies, and tool state. Its verifier checks the archive independently, scans allowed text for secret patterns, and requires the empty runtime directory structure.

---

# 25. Security audit

```powershell
python scripts/security_audit.py
```

Report:

```text
data/reports/security_audit.json
```

The runtime security report checks:

- localhost-only server binding
- absence of the broad `<all_urls>` extension permission
- absence of the configured provider key from extension files, active-mode exports, and logs

Separate self-test, regression-test, manifest, and release-verifier gates check the
collector-token location and entropy, mutation-token and same-origin protections,
wildcard CORS absence, source/ZIP secret patterns, and exclusion of `.env`, SQLite
files and sidecars, private evidence, collected assets, logs, caches, dependencies,
tool state, and tokens from the release ZIP.

The application deliberately does not enable wildcard CORS.

State-changing extension API requests require:

```http
X-GuardianLens-Token: <local token>
```

State-changing dashboard forms require the same local secret as a hidden form token;
review GET routes are read-only. Private evidence routes enforce same-origin requests,
loopback Host/client boundaries, path containment, and regular-file checks.

---

# 26. Data-integrity audit

```powershell
python scripts/data_integrity_audit.py
```

Report:

```text
data/reports/data_integrity_audit.json
```

Current checks include:

- SQLite quick-check and foreign-key integrity
- exact status/history consistency and legal lifecycle transitions
- stale/interrupted processing runs and review sessions
- approved records have required clean fields and no residual PII pattern
- source URL, marketplace ID, metadata, and exact-image SHA-256 duplicate integrity
- image row ownership, path containment, file existence, decodability, dimensions, byte size, SHA-256, and perceptual hash
- orphan image/screenshot/staging rows, files, and listing folders
- screenshot-retention and cleanup failures
- approved-only export prerequisites and soft-test path isolation

Run this periodically and again before declaring collection complete.

---

# 27. Collection completion rule

Do **not** stop at:

```text
2,500 captured
```

Stop the collection phase only when the approved dataset has approximately:

```text
Approved total: 2,500
Carousell:      1,500
Mudah:          1,000
```

and your configured category coverage is acceptable.

Then run:

```powershell
python scripts/security_audit.py
python scripts/data_integrity_audit.py
python scripts/export_approved.py
```

The next phase should work from the exported/frozen approved dataset, not directly from captured drafts.

---

# 28. Troubleshooting

## `conda` is not recognized

Use **Anaconda Prompt**, or initialize Conda for PowerShell:

```powershell
conda init powershell
```

Close and reopen the terminal.

## Port 8765 is already in use

Check Windows:

```powershell
netstat -ano | findstr :8765
```

If you intentionally change `PORT` in `.env`, also change the local server URL in the extension popup.

## Extension says invalid token

1. Start GuardianLens.
2. Refresh the dashboard.
3. Copy the displayed local collector token.
4. Paste it into the extension.
5. Save settings.

The token file is private:

```text
data/private/local_token.txt
```

Do not put any provider API key there.

## Extension says open a Carousell or Mudah listing

The active tab hostname must be Carousell Malaysia or Mudah. The collector intentionally rejects unrelated sites.

## AI processing fails with a missing API key

Set `AI_PROVIDER` and only its matching key, for example:

```env
AI_PROVIDER=openai
OPENAI_API_KEY=...
```

Restart the server.

The capture itself does not require any AI provider. You can continue manually capturing
pages and process them later. Authentication, quota, unsupported-model, rate-limit,
timeout, network, and invalid-response failures appear with safe classifications on the
dashboard; credentials are redacted.

## `ai_failed`

The capture stays in the database. Fix the API/network/model configuration, then process the queue again.

## `image_failed`

One of the selected product-image resources could not be downloaded or validated. The record is not silently accepted. Reopen the listing, verify the images, then retry/re-capture as appropriate.

## `needs_attention`

Open the review record. Typical reasons:

- missing product images
- missing required field
- category outside configured list
- residual PII pattern
- exact image duplicate
- visually similar image

## `duplicate_blocked`

The collector found an exact duplicate condition that should not enter the approved dataset unchanged.

---

# 29. What was deliberately left out

Do not add these to this collector unless the research design changes:

- automatic marketplace listing discovery
- search crawling
- pagination crawling
- unattended seller crawling
- fraud/legitimate labels
- fraud score
- model confidence score
- AI confidence score
- annotation/adjudication tables
- train/validation/test split
- ML training/evaluation

Those are later GuardianLens phases.

---

# 30. Recommended first real run

After the automated tests pass:

```text
1. setup Conda environment
2. set `AI_PROVIDER` and the matching provider key in `.env`
3. run production server once and inspect dashboard
4. install Chrome extension
5. copy local token into extension
6. stop production server
7. start soft-test server
8. manually capture exactly 1 Carousell listing
9. manually capture exactly 1 Mudah listing
10. process max 2
11. review both
12. correct AI mistakes
13. approve both only when clean
14. inspect soft-test DB/dashboard
15. reset soft-test DB if desired
16. start production server
17. begin real manual collection
18. process captures in controlled batches
19. review every record
20. audit and export only approved records
```

That is the intended operating procedure for this package.
# GuardianLens_Data_Collector
