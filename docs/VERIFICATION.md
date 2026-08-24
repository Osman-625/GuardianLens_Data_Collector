# Verification Procedure

## 1. Activate the project environment

Run from the repository root:

```powershell
conda activate guardianlens-collector
python --version
```

The verified environment uses Python 3.12. `environment.yml` also supplies `tzdata` so
`OPERATOR_TIMEZONE=Asia/Kuala_Lumpur` works on a clean Windows installation.

## 2. Source-tree gates

```powershell
python -m compileall -q src\guardianlens tests
python -m pytest -q -p no:cacheprovider --basetemp=.tmp\pytest-verification
node --check src\guardianlens\static\app.js
node --check extension\background.js
node --check extension\popup.js
python scripts\self_test.py
python scripts\mock_integration_check.py
```

`self_test.py` creates its schema-check database in an OS temporary directory.
`mock_integration_check.py` creates its synthetic database, screenshots, staging, product
images, exports, and reports in another disposable project. Neither resets or writes the
persistent production or soft-test runtime.

## 3. Dataset audits

For the active mode:

```powershell
python scripts\security_audit.py
python scripts\data_integrity_audit.py
```

These commands write JSON reports under the active mode's `reports/` directory. To audit the
isolated soft-test dataset from a second PowerShell window:

```powershell
$env:GUARDIANLENS_MODE = 'soft_test'
python scripts\security_audit.py
python scripts\data_integrity_audit.py
```

That environment variable remains in the current PowerShell window. Close it or run
`Remove-Item Env:GUARDIANLENS_MODE` before starting production. The local collector token
and dashboard provider override are shared; databases and runtime collection assets/outputs
are isolated.

Do not copy or rename a soft-test database into production. There is intentionally no
automatic production-reset utility.

## 4. Manifest and distributable

Run these only after the final source and documentation edit:

```powershell
python scripts\manifest.py --generate
python scripts\manifest.py --verify
python scripts\build_release_zip.py
python scripts\verify_fresh_release.py
```

The ZIP uses a strict source allowlist and must include `setup.bat`, `run.bat`, the source,
tests, configuration, docs, extension, verification scripts, manifest, and empty runtime
directory markers. It excludes `.env`, credentials, tokens, provider overrides, production
and soft-test databases/sidecars, logs, screenshots, staging, collected images, generated
exports/reports, caches, dependencies, temporary data, and agent/tool state.

The independent ZIP verifier checks names, paths, duplicates, symlinks, size limits, required
files/directories, exact manifest coverage/hashes, secret patterns, and runtime-file
injection. Fresh verification extracts to a new OS temporary directory and runs self-test,
the complete pytest suite, and the synthetic two-record integration there.

## 5. Fresh extracted startup

After ZIP verification, extract it to a new empty directory and run:

```powershell
.\setup.bat
$env:GUARDIANLENS_MODE = 'soft_test'
$env:PORT = '8876'
python main.py
```

Verify `http://127.0.0.1:8876/health` reports `soft_test` and the dashboard loads. Stop that
server before continuing. This startup must create data only inside the fresh extraction.

## 6. Required live two-listing soft test

Start `python scripts\run_soft_test_server.py`, configure the extension with the local token,
then manually capture exactly:

1. one individual Carousell Malaysia listing;
2. one individual Mudah.my listing.

For each, confirm capture → queue → selected provider/model → normalized extraction → PII →
images → duplicates → ready for review → full evidence/form → human correction → approval →
complete history → approved/planner increment. Then run the soft-test security/integrity
audits and approved export. Do not begin large real collection until both pass.

Live marketplace DOM compatibility and real provider authentication/quota/network behavior
cannot be certified by offline tests.
