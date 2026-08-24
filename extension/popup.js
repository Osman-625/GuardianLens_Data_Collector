function normalizeServer(value) {
  const match = /^http:\/\/(127\.0\.0\.1|localhost):(\d{1,5})\/?$/i.exec(String(value || '').trim());
  if (!match) return null;
  const port = Number(match[2]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  return `http://${match[1].toLowerCase()}:${port}`;
}

if (typeof module !== 'undefined') {
  module.exports = {normalizeServer};
}

if (typeof document !== 'undefined' && typeof chrome !== 'undefined') {
  const server = document.getElementById('server');
  const token = document.getElementById('token');
  const statusBox = document.getElementById('status');

  chrome.storage.local.get(['guardianlensServer', 'guardianlensToken'], values => {
    server.value = normalizeServer(values.guardianlensServer) || 'http://127.0.0.1:8765';
    token.value = values.guardianlensToken || '';
  });

  document.getElementById('save').addEventListener('click', async () => {
    const normalized = normalizeServer(server.value);
    if (!normalized) {
      statusBox.textContent = 'Server must be an exact localhost URL with port 1–65535, for example http://127.0.0.1:8765';
      return;
    }
    const cleanToken = token.value.trim();
    server.value = normalized;
    token.value = cleanToken;
    try {
      await chrome.storage.local.set({
        guardianlensServer: normalized,
        guardianlensToken: cleanToken,
      });
      statusBox.textContent = 'Settings saved locally in Chrome.';
    } catch (error) {
      statusBox.textContent = `ERROR: Could not save settings: ${error.message}`;
    }
  });

  const captureButton = document.getElementById('capture');
  captureButton.addEventListener('click', async () => {
    if (captureButton.disabled) return;
    captureButton.disabled = true;
    statusBox.textContent = 'Capturing current tab…';
    try {
      const normalized = normalizeServer(server.value);
      if (!normalized) {
        statusBox.textContent = 'Invalid local server URL.';
        return;
      }
      const cleanToken = token.value.trim();
      if (!cleanToken) {
        statusBox.textContent = 'Paste the local collector token first.';
        return;
      }
      server.value = normalized;
      token.value = cleanToken;
      await chrome.storage.local.set({
        guardianlensServer: normalized,
        guardianlensToken: cleanToken,
      });
      const response = await chrome.runtime.sendMessage({
        type: 'guardianlens.capture',
        server: normalized,
        token: cleanToken,
      });
      if (!response?.ok) throw new Error(response?.error || 'Capture failed');
      statusBox.textContent = response.duplicate
        ? `Duplicate blocked. Existing record: ${response.listing_id}`
        : `Captured. Listing ID: ${response.listing_id}\nStatus: ${response.status}`;
    } catch (error) {
      statusBox.textContent = `ERROR: ${error.message}`;
    } finally {
      captureButton.disabled = false;
    }
  });
}
