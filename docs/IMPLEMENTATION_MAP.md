# Implementation Map

This file maps the approved collection design to code.

| Design requirement | Implementation |
|---|---|
| Manual listing selection | Chrome `activeTab` capture only, `extension/background.js`; pre/post active-tab identity and URL checks prevent screenshot/tab races |
| Individual-listing validation | `extension/background.js`, `capture.py`; exact host + URL pattern + listing-specific evidence |
| No crawler | No marketplace search/pagination/seller traversal code or broad host permissions; batch selects only `captured` rows |
| Local backend | `src/guardianlens/app.py`, default `127.0.0.1:8765`; loopback Host/client middleware and security headers |
| Staging | `capture.py`, private screenshot + bounded private staging JSON with rollback cleanup |
| AI form extraction (OpenAI/Anthropic/OpenRouter/Gemini, `AI_PROVIDER`-selected) | `openai_extractor.py`, `anthropic_extractor.py`, `openrouter_extractor.py`, `gemini_extractor.py`, `extraction_contract.py` |
| No AI confidence | Extraction schema/model rejects extra fields, no confidence DB column |
| Provider failures/retry | `errors.py`, `batch.py`; redacted safe classifications, bounded transient retry/backoff, permanent configuration halt |
| PII scrub | `pii.py`, applied to extraction and every human-editable free-text field; residual approval gate |
| Image integrity | `images.py`; HTTPS/public-host checks, redirect/size/pixel limits, safe re-encode, hashes, atomic file choreography |
| URL/metadata duplicates | `dedupe.py`, `capture.py`, `processor.py`, `review.py` |
| Image duplicates | SHA-256 and perceptual hash in `images.py` / `processor.py` |
| Category/platform planner | `planner.py` + YAML config |
| State machine | `states.py` |
| Human review | `review.py` + review/record templates; read-only GET, explicit begin, five outcomes, corrections/provenance/history |
| Atomic approval | `review.edit_and_approve()`/`approve()` use `BEGIN IMMEDIATE` for final fields, PII/images/duplicates, corrections, state, and history |
| Batch controls | `batch.py` |
| Crash recovery | `BatchManager.recover()` at FastAPI startup; explicit `review_session_interrupted` audit markers exclude offline time |
| Audit trail | `status_history`, `review_actions`, `ai_extractions`, `processing_runs` |
| Private screenshots | `data/private/screenshots/` and protected asset route |
| Product images | `data/images/<platform>/<listing_id>/` |
| Dashboard/count consistency | `dashboard.py`, `/api/dashboard/status`; one read snapshot, exact lifecycle counts, planner, review/run metrics, 4-second partial polling |
| Full lifecycle visibility | `dashboard.records_page()`, `templates/records.html`; searchable/filterable/paginated terminal and failure records |
| SQLite migration/integrity | `db.py`, `schema.sql`; versioned migration, online backup, locked version re-check, schema/FK/index/history validation |
| Soft-test isolation | `GUARDIANLENS_MODE=soft_test`; separate SQLite, screenshots, staging, images, temporary files, exports, and reports |
| Local request token | `security.py`; API header token, dashboard form token, atomic private token file, constant-time comparison |
| No wildcard CORS | No permissive CORS middleware configured |
| Private asset boundary | `app.py`, `security.py`; same-origin check, containment, regular-file check, no-store headers |
| Security/data audits | `audit.py`, `scripts/security_audit.py`, `scripts/data_integrity_audit.py` |
| Approved export | `exports.py`; approved-only snapshot, provenance/corrections/history, formula neutralization, atomic CSV-last pair, no screenshots |
| Release hygiene | `release.py`, `scripts/manifest.py`, `scripts/build_release_zip.py`, `scripts/verify_fresh_release.py`; strict allowlist, secret scan, manifest/ZIP/fresh extraction |
| Windows entry points | `setup.bat` and `run.bat`; both required in the release ZIP |
