const tokenElement = document.getElementById('token') || document.querySelector('input[name="token"]');
const GL_TOKEN = tokenElement
  ? String('value' in tokenElement ? tokenElement.value : tokenElement.textContent).trim()
  : '';

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? '');
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (GL_TOKEN) headers.set('X-GuardianLens-Token', GL_TOKEN);
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return body;
}

function appendCell(row, value) {
  const cell = document.createElement('td');
  cell.textContent = value ?? '—';
  row.appendChild(cell);
  return cell;
}

function listingLabel(item) {
  return item.title || item.page_title || item.listing_id;
}

function appendRecordLink(cell, item) {
  cell.textContent = '';
  const link = document.createElement('a');
  link.href = `/records/${encodeURIComponent(item.listing_id)}`;
  link.textContent = listingLabel(item);
  cell.appendChild(link);
}

function renderBatch(status) {
  const details = document.getElementById('batchStatus');
  if (details) details.textContent = JSON.stringify(status, null, 2);

  const run = status.latest_run;
  const fill = document.getElementById('progressFill');
  const label = document.getElementById('progressLabel');
  const progressbar = fill ? fill.parentElement : null;
  if (!fill || !label) return;

  if (!run) {
    fill.style.width = '0%';
    if (progressbar) progressbar.setAttribute('aria-valuenow', '0');
    label.textContent = 'Idle — no processing run yet.';
    return;
  }

  const complete = !['running', 'paused', 'stopping'].includes(run.status);
  const percent = run.item_limit
    ? Math.min(100, Math.round((100 * run.attempted) / run.item_limit))
    : complete
      ? 100
      : 0;
  fill.style.width = `${percent}%`;
  if (progressbar) progressbar.setAttribute('aria-valuenow', String(percent));
  const command = status.control?.command || run.status;
  label.textContent = `${command} — run ${run.run_id.slice(0, 8)} — ${run.attempted}/${run.item_limit || 'all'} attempted (${run.status})`;
  setText('cAttempted', run.attempted);
  setText('cSuccessful', run.successful);
  setText('cAttention', run.needs_attention);
  setText('cAiFail', run.ai_failures);
  setText('cImgFail', run.image_failures);
  setText('cDup', run.duplicates);
}

function renderAiStatus(status) {
  document.querySelectorAll('.provider-pill').forEach((button) => {
    const active = button.dataset.provider === status.active;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    button.textContent = `${button.dataset.provider}${active ? ' ★' : ''}`;
  });
  const provider = status.providers.find((candidate) => candidate.active);
  setText('providerModel', provider ? `Active model: ${provider.model}` : 'No active provider is configured.');
}

function renderRecommendations(recommendations) {
  const container = document.getElementById('recommendations');
  if (!container) return;
  container.textContent = '';
  if (!recommendations.length) {
    const message = document.createElement('p');
    message.textContent = 'Configured targets are currently covered by approved and pending records.';
    container.appendChild(message);
    return;
  }
  recommendations.forEach((recommendation) => {
    const article = document.createElement('article');
    const title = document.createElement('b');
    title.textContent = `${recommendation.platform[0].toUpperCase()}${recommendation.platform.slice(1)} → ${recommendation.category}`;
    const detail = document.createElement('small');
    detail.textContent = `Estimated remaining ${recommendation.remaining}`;
    article.append(title, detail);
    container.appendChild(article);
  });
}

function renderCategories(categories) {
  const body = document.getElementById('categoryRows');
  if (!body) return;
  body.textContent = '';
  categories.forEach((category) => {
    const row = document.createElement('tr');
    appendCell(row, category.name);
    appendCell(row, category.approved);
    appendCell(row, category.pending);
    appendCell(row, category.target);
    appendCell(row, category.remaining);
    body.appendChild(row);
  });
}

let lastOperationalQueueSignature = null;

function renderOperationalQueue(items) {
  const body = document.getElementById('operationalQueueRows');
  if (!body) return;
  const signature = JSON.stringify(
    items.map((item) => [
      item.listing_id,
      item.status,
      item.updated_at,
      item.title,
      item.page_title,
      item.ai_error,
      item.status_reason,
    ]),
  );
  if (signature === lastOperationalQueueSignature) return;
  lastOperationalQueueSignature = signature;
  const active = document.activeElement;
  const focusedRow = active?.closest?.('tr');
  const focusedListingId = focusedRow?.dataset.listingId || null;
  const focusedSelector = active?.classList?.contains('retry-listing') ? '.retry-listing' : active?.tagName === 'A' ? 'a' : null;
  const selected = new Set(
    [...body.querySelectorAll('.retry-listing:checked')].map((input) => input.value),
  );
  body.textContent = '';
  if (!items.length) {
    const row = document.createElement('tr');
    const cell = appendCell(row, 'No captured, processing, failed, or duplicate-blocked records.');
    cell.colSpan = 6;
    body.appendChild(row);
    if (focusedListingId) document.getElementById('retrySelected')?.focus();
    return;
  }
  const retryable = new Set(['processing_failed', 'ai_failed', 'image_failed']);
  items.forEach((item) => {
    const row = document.createElement('tr');
    row.dataset.listingId = item.listing_id;
    const selection = document.createElement('td');
    if (retryable.has(item.status)) {
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'retry-listing';
      checkbox.value = item.listing_id;
      checkbox.checked = selected.has(item.listing_id);
      checkbox.setAttribute('aria-label', `Select ${listingLabel(item)} for retry`);
      selection.appendChild(checkbox);
    }
    row.appendChild(selection);
    appendCell(row, `${item.platform[0].toUpperCase()}${item.platform.slice(1)}`);
    appendRecordLink(appendCell(row, ''), item);
    const statusCell = appendCell(row, '');
    const status = document.createElement('span');
    status.className = 'status';
    status.textContent = item.status;
    statusCell.appendChild(status);
    appendCell(row, item.ai_error || item.status_reason || '—');
    appendCell(row, item.captured_at);
    body.appendChild(row);
  });
  if (focusedListingId && focusedSelector) {
    const replacement = [...body.rows]
      .find((row) => row.dataset.listingId === focusedListingId)
      ?.querySelector(focusedSelector);
    (replacement || document.getElementById('retrySelected'))?.focus();
  }
}

let lastActivitySignature = null;

function renderActivity(items) {
  const body = document.getElementById('activityRows');
  if (!body) return;
  const signature = JSON.stringify(
    items.map((item) => [
      item.history_id,
      item.listing_id,
      item.timestamp,
      item.title,
      item.page_title,
      item.from_status,
      item.to_status,
      item.reason,
    ]),
  );
  if (signature === lastActivitySignature) return;
  lastActivitySignature = signature;
  const active = document.activeElement;
  const focusedRow = active?.closest?.('tr');
  const focusedHistoryId = active?.tagName === 'A' ? focusedRow?.dataset.historyId : null;
  body.textContent = '';
  if (!items.length) {
    const row = document.createElement('tr');
    const cell = appendCell(row, 'No lifecycle activity yet.');
    cell.colSpan = 4;
    body.appendChild(row);
    if (focusedHistoryId) document.getElementById('activity-heading')?.focus();
    return;
  }
  items.forEach((item) => {
    const row = document.createElement('tr');
    row.dataset.listingId = item.listing_id;
    row.dataset.historyId = item.history_id;
    appendCell(row, item.timestamp);
    appendRecordLink(appendCell(row, ''), item);
    appendCell(row, `${item.from_status || 'new'} → ${item.to_status}`);
    appendCell(row, item.reason || '—');
    body.appendChild(row);
  });
  if (focusedHistoryId) {
    const replacement = [...body.rows]
      .find((row) => row.dataset.historyId === focusedHistoryId)
      ?.querySelector('a');
    (replacement || document.getElementById('activity-heading'))?.focus();
  }
}

function renderDashboard(snapshot) {
  Object.entries(snapshot.counts).forEach(([status, count]) => setText(`count-${status}`, count));
  setText('approvedTotal', snapshot.plan.approved_total);
  setText('datasetTarget', snapshot.plan.total_target);
  const carousell = snapshot.plan.platforms.carousell;
  const mudah = snapshot.plan.platforms.mudah;
  if (carousell) {
    setText('carousellApproved', carousell.approved);
    setText('carousellTarget', carousell.target);
    setText('carousellPending', carousell.pending);
  }
  if (mudah) {
    setText('mudahApproved', mudah.approved);
    setText('mudahTarget', mudah.target);
    setText('mudahPending', mudah.pending);
  }
  setText('reviewOpen', snapshot.review_stats.open_count);
  setText('reviewedToday', snapshot.review_stats.reviewed_today);
  setText('reviewTimezone', snapshot.review_stats.operator_timezone);
  const hasReviewHistory = snapshot.review_stats.completed_sessions > 0;
  setText(
    'reviewAverage',
    hasReviewHistory ? `${Math.round(snapshot.review_stats.average_seconds)} sec` : 'Not enough history',
  );
  setText(
    'reviewEstimate',
    hasReviewHistory
      ? `${Math.floor(snapshot.review_stats.estimated_remaining_seconds / 60)} min`
      : 'Not enough history',
  );
  setText('unassignedCategoryPending', snapshot.plan.unassigned_category_pending);
  setText('unconfiguredCategoryApproved', snapshot.plan.unconfigured_category_approved);
  renderRecommendations(snapshot.plan.recommendations);
  renderCategories(snapshot.plan.categories);
  renderOperationalQueue(snapshot.operational_queue);
  renderActivity(snapshot.activity);
  renderBatch(snapshot.batch);
  renderAiStatus(snapshot.ai_status);
}

let lastReviewQueueSignature = null;

function renderReviewQueue(items, total = items.length) {
  const body = document.getElementById('reviewQueueRows');
  if (!body) return;
  setText('reviewQueueCount', total);
  setText('reviewQueueShown', items.length);
  const signature = JSON.stringify(
    items.map((item) => [
      item.listing_id,
      item.status,
      item.updated_at,
      item.title,
      item.category,
      item.price,
      item.currency,
    ]),
  );
  if (signature === lastReviewQueueSignature) return;
  lastReviewQueueSignature = signature;
  const active = document.activeElement;
  const focusedRow = active?.closest?.('tr');
  const focusedListingId = focusedRow?.dataset.listingId || null;
  const focusedSelector = active?.tagName === 'A' ? 'a' : active?.tagName === 'BUTTON' ? 'button' : null;
  body.textContent = '';
  const empty = document.getElementById('reviewQueueEmpty');
  if (empty) empty.hidden = total > 0;

  items.forEach((item) => {
    const row = document.createElement('tr');
    row.dataset.listingId = item.listing_id;
    appendCell(row, `${item.platform[0].toUpperCase()}${item.platform.slice(1)}`);
    appendRecordLink(appendCell(row, ''), item);
    appendCell(row, item.category || 'Not extracted');
    const price = item.price == null ? 'Not extracted' : `${item.currency || 'Not extracted'} ${Number(item.price).toFixed(2)}`;
    appendCell(row, price);
    const statusCell = appendCell(row, '');
    const status = document.createElement('span');
    status.className = 'status';
    status.textContent = item.status;
    statusCell.appendChild(status);
    const actionCell = appendCell(row, '');
    if (item.status === 'under_review') {
      const link = document.createElement('a');
      link.className = 'button';
      link.href = `/review/${encodeURIComponent(item.listing_id)}`;
      link.textContent = 'Continue';
      actionCell.appendChild(link);
    } else {
      const form = document.createElement('form');
      form.method = 'post';
      form.action = `/review/${encodeURIComponent(item.listing_id)}/begin`;
      const hidden = document.createElement('input');
      hidden.type = 'hidden';
      hidden.name = 'token';
      hidden.value = GL_TOKEN;
      const button = document.createElement('button');
      button.type = 'submit';
      button.textContent = 'Begin review';
      form.append(hidden, button);
      actionCell.appendChild(form);
    }
    body.appendChild(row);
  });
  if (focusedListingId && focusedSelector) {
    const replacement = [...body.rows]
      .find((row) => row.dataset.listingId === focusedListingId)
      ?.querySelector(focusedSelector);
    (replacement || document.getElementById('reviewQueueHeading'))?.focus();
  }
}

async function refreshDashboard() {
  try {
    renderDashboard(await api('/api/dashboard/status'));
  } catch (error) {
    setText('progressLabel', `Dashboard refresh failed: ${error.message}`);
  }
}

async function refreshReviewQueue() {
  const errorMessage = document.getElementById('reviewQueueError');
  try {
    const response = await api('/api/review/queue');
    if (errorMessage) {
      errorMessage.hidden = true;
      errorMessage.textContent = '';
    }
    renderReviewQueue(response.rows, response.total);
  } catch (error) {
    if (errorMessage) {
      errorMessage.hidden = false;
      errorMessage.textContent = `Review queue refresh failed: ${error.message}`;
    }
  }
}

async function startBatch() {
  const durationValue = document.getElementById('duration').value;
  const limitValue = document.getElementById('limit').value;
  try {
    await api('/api/batch/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        duration_minutes: durationValue ? Number(durationValue) : null,
        item_limit: limitValue ? Number(limitValue) : null,
      }),
    });
    await refreshDashboard();
  } catch (error) {
    setText('progressLabel', `Could not start batch: ${error.message}`);
  }
}

async function sendBatchCommand(command) {
  try {
    renderBatch(await api(`/api/batch/${command}`, { method: 'POST' }));
  } catch (error) {
    setText('progressLabel', `Could not ${command} batch: ${error.message}`);
  }
}

let modelRequestGeneration = 0;

async function switchProvider(provider) {
  const providerButtons = [...document.querySelectorAll('.provider-pill')];
  const previousDisabledStates = providerButtons.map((button) => button.disabled);
  providerButtons.forEach((button) => {
    button.disabled = true;
  });
  modelRequestGeneration += 1;
  try {
    const status = await api('/api/ai/provider', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider }),
    });
    renderAiStatus(status);
    const models = document.getElementById('providerModels');
    if (models) models.textContent = '';
  } catch (error) {
    setText('providerModel', `Could not switch provider: ${error.message}`);
  } finally {
    providerButtons.forEach((button, index) => {
      button.disabled = previousDisabledStates[index];
    });
  }
}

async function loadModels() {
  const active = document.querySelector('.provider-pill.active')?.dataset.provider;
  const list = document.getElementById('providerModels');
  if (!list || !active) return;
  const requestGeneration = ++modelRequestGeneration;
  list.textContent = '';
  const loading = document.createElement('li');
  loading.textContent = `Loading ${active} models…`;
  list.appendChild(loading);
  try {
    const response = await api(`/api/ai/models/${encodeURIComponent(active)}`);
    const currentProvider = document.querySelector('.provider-pill.active')?.dataset.provider;
    if (requestGeneration !== modelRequestGeneration || currentProvider !== active) return;
    list.textContent = '';
    response.models.forEach((model) => {
      const item = document.createElement('li');
      item.textContent = `${model}${model === response.configured_model ? ' ← configured' : ''}`;
      list.appendChild(item);
    });
  } catch (error) {
    const currentProvider = document.querySelector('.provider-pill.active')?.dataset.provider;
    if (requestGeneration !== modelRequestGeneration || currentProvider !== active) return;
    list.textContent = '';
    const item = document.createElement('li');
    item.textContent = `Could not load models: ${error.message}`;
    list.appendChild(item);
  }
}

async function retrySelected() {
  const listingIds = [...document.querySelectorAll('.retry-listing:checked')].map((input) => input.value);
  if (!listingIds.length) {
    setText('progressLabel', 'Select at least one eligible failed listing to retry.');
    return;
  }
  try {
    const result = await api('/api/queue/retry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ listing_ids: listingIds }),
    });
    setText('progressLabel', `Queued ${result.retried.length} listing(s) for retry${result.rejected.length ? `; ${result.rejected.length} rejected` : ''}.`);
    await refreshDashboard();
  } catch (error) {
    setText('progressLabel', `Could not retry listings: ${error.message}`);
  }
}

async function exportApproved() {
  try {
    const result = await api('/api/export', { method: 'POST' });
    setText('exportStatus', `Exported: ${result.files.join(', ')}`);
  } catch (error) {
    setText('exportStatus', `Export failed: ${error.message}`);
  }
}

document.getElementById('batchStart')?.addEventListener('click', startBatch);
document.querySelectorAll('[data-batch-command]').forEach((button) => {
  button.addEventListener('click', () => sendBatchCommand(button.dataset.batchCommand));
});
document.querySelectorAll('.provider-pill').forEach((button) => {
  button.addEventListener('click', () => switchProvider(button.dataset.provider));
});
document.getElementById('loadProviderModels')?.addEventListener('click', loadModels);
document.getElementById('retrySelected')?.addEventListener('click', retrySelected);
document.getElementById('exportApproved')?.addEventListener('click', exportApproved);
document.getElementById('copyToken')?.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(GL_TOKEN);
    setText('exportStatus', 'Extension token copied.');
  } catch (_error) {
    setText('exportStatus', 'Copy failed. Select the token and copy it manually.');
  }
});

const reviewEditForm = document.getElementById('reviewEditForm');
if (reviewEditForm) {
  let reviewIsDirty = false;
  const approveUnchanged = document.getElementById('approveUnchanged');
  const unsavedNotice = document.getElementById('unsavedReviewNotice');
  const markDirty = () => {
    reviewIsDirty = true;
    if (approveUnchanged) approveUnchanged.disabled = true;
    if (unsavedNotice) unsavedNotice.hidden = false;
  };
  reviewEditForm.addEventListener('input', markDirty);
  reviewEditForm.addEventListener('submit', () => {
    reviewIsDirty = false;
  });
  document.querySelectorAll('[data-discard-edits]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (reviewIsDirty && !window.confirm('This outcome will discard your unsaved edits. Continue?')) {
        event.preventDefault();
        return;
      }
      reviewIsDirty = false;
    });
  });
  window.addEventListener('beforeunload', (event) => {
    if (!reviewIsDirty) return;
    event.preventDefault();
    event.returnValue = '';
  });
}

if (document.getElementById('dashboard-heading')) {
  refreshDashboard();
  setInterval(() => {
    if (!document.hidden) refreshDashboard();
  }, 4000);
}

if (document.getElementById('reviewQueueRows')) {
  refreshReviewQueue();
  setInterval(() => {
    if (!document.hidden) refreshReviewQueue();
  }, 4000);
}
