# Data and Research Boundary

## Manual collection boundary

The researcher manually searches the marketplace, chooses an individual listing, opens it,
and presses **Capture Current Listing**. GuardianLens does not search, crawl, paginate,
discover URLs, traverse seller profiles, select listings, or approve records unattended.
Batch processing begins only after manual capture and selects only `captured` rows.

## Approved research data

Normal approved exports may contain cleaned listing text, platform/category metadata,
research-relevant visible seller behaviour, product-image metadata/references, source
provenance, normalized AI provider/model/prompt provenance, status history, and human
correction/review audit data. Only `approved` records count toward 2,500.

## Private collection evidence

Listing screenshots and private staging material are capture/review evidence. They live under
`data/private/` in production or `data/soft_test/runtime/private/` in soft-test mode. They
are served only through protected same-origin localhost routes and are never included in
ordinary dataset exports or the release ZIP. Retention follows `SCREENSHOT_RETENTION`.

## Product images

Product images are research assets, not screenshots. They are stored separately under
`data/images/<platform>/<listing_id>/` (or the isolated soft-test equivalent) only after
HTTPS/public-host validation, size/pixel/decode checks, safe metadata-stripping re-encode,
hashing, duplicate checks, and atomic publication.

## Secrets and local runtime data

Provider API keys stay only in `.env`. The independent collector token and provider override
stay under `data/private/`. Databases, WAL/SHM files, logs, screenshots, staging data,
collected images, exports, reports, secrets, caches, and test/tool state are runtime data and
must not enter the distributable ZIP.

## Soft-test isolation

The real two-listing trial uses `GUARDIANLENS_MODE=soft_test`, with a separate database and
separate screenshots, staging, images, temporary files, exports, and reports. Soft-test
records never count toward production targets. The localhost collector token and the
operator's dashboard-selected provider override are shared configuration, not dataset
content; all captured/processed/reviewed data and outputs remain mode-isolated.

## Excluded from this phase

Fraud/legitimate labels, annotation/adjudication, train/validation/test assignments, model
training/evaluation, model predictions, fraud scores, AI confidence, calibrated confidence,
and automatic marketplace discovery belong to later GuardianLens phases.
