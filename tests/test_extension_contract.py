from __future__ import annotations

import json
import shutil
import subprocess

import pytest


def test_extension_manifest_is_manual_and_least_privilege(isolated):
    manifest = json.loads((isolated / "extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {"activeTab", "scripting", "storage"}
    assert set(manifest["host_permissions"]) == {
        "http://127.0.0.1/*",
        "http://localhost/*",
    }
    serialized = json.dumps(manifest)
    assert "<all_urls>" not in serialized
    assert "tabs" not in manifest["permissions"]


def test_popup_has_accessible_async_status(isolated):
    from guardianlens.config import ROOT

    popup = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
    assert '<html lang="en">' in popup
    assert "<title>GuardianLens Capture</title>" in popup
    assert 'role="status"' in popup
    assert 'aria-live="polite"' in popup
    assert 'aria-atomic="true"' in popup


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is optional")
def test_popup_and_service_worker_reject_deceptive_local_server_urls(isolated):
    # conftest copies only manifest.json into the isolated extension tree.
    # Validation logic is read from the repository because it has no runtime-data dependency.
    from guardianlens.config import ROOT

    popup_path = ROOT / "extension" / "popup.js"
    background_path = ROOT / "extension" / "background.js"
    script = r"""
const popup = require(process.argv[1]);
const background = require(process.argv[2]);
const valid = [
  ['http://127.0.0.1:1', 'http://127.0.0.1:1'],
  ['http://localhost:80', 'http://localhost:80'],
  [' http://localhost:65535 ', 'http://localhost:65535'],
  ['http://127.0.0.1:8765/', 'http://127.0.0.1:8765'],
];
const invalid = [
  'http://127.0.0.1:0',
  'http://127.0.0.1:65536',
  'http://localhost',
  'http://localhost:8765/path',
  'http://localhost:8765?query=1',
  'http://user@localhost:8765',
  'http://localhost.evil.test:8765',
  'https://localhost:8765',
  'http://[::1]:8765',
];
for (const [input, expected] of valid) {
  if (popup.normalizeServer(input) !== expected) process.exit(11);
  if (background.normalizeLocalServer(input) !== expected) process.exit(12);
}
for (const input of invalid) {
  if (popup.normalizeServer(input) !== null) process.exit(21);
  if (background.normalizeLocalServer(input) !== null) process.exit(22);
}
const original = {id: 7, windowId: 3, url: 'https://www.mudah.my/test-123456.htm'};
if (!background.tabSnapshotMatches({...original}, original, original.url)) process.exit(31);
if (background.tabSnapshotMatches({...original, id: 8}, original, original.url)) process.exit(32);
if (background.tabSnapshotMatches({...original, url: 'https://www.mudah.my/other-999999.htm'}, original, original.url)) process.exit(33);
const detail = background.safeApiErrorDetail(
  [{loc: ['body', 'page_title'], msg: 'String should have at most 500 characters'}],
  'fallback',
);
if (!detail.includes('page_title') || detail.includes('[object Object]')) process.exit(34);
"""
    result = subprocess.run(
        ["node", "-e", script, str(popup_path), str(background_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
