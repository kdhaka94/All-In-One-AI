// Operations screen: pick one record, investigate it under a record scope, read a
// brief whose every claim links back to the query that produced it.

const OPS_PROFILES = [
  {
    id: "fineract:learned",
    label: "Fineract Core Banking · Learned catalog",
    configPath: "config/fineract.example.yaml",
    catalogPath: "config/fineract_catalog.json",
    schemaSource: "learned",
  },
  {
    id: "fineract:runtime",
    label: "Fineract Core Banking · Live schema",
    configPath: "config/fineract.example.yaml",
    catalogPath: "config/fineract_catalog.json",
    schemaSource: "runtime",
  },
];

const THEME_STORAGE_KEY = "db-agent-theme";

// A profile can also be named in the URL (/ops?config=config/my.yaml), so a
// deployment with its own config file does not have to be listed above.
function profilesForLocation() {
  const requested = new URLSearchParams(window.location.search).get("config");
  if (!requested) return OPS_PROFILES;
  return [
    {
      id: "url",
      label: requested,
      configPath: requested,
      catalogPath: null,
      schemaSource: "runtime",
    },
    ...OPS_PROFILES,
  ];
}

const PROFILES = profilesForLocation();

const opsState = {
  profile: PROFILES[0],
  records: [],
  keyColumn: "id",
  labelColumns: [],
  defaultQuestion: "",
  selected: null,
  evidence: [],
  busy: false,
};

const el = (id) => document.getElementById(id);
const profileSelect = el("opsProfile");
const searchInput = el("opsSearch");
const recordList = el("opsRecords");
const recordCount = el("opsRecordCount");
const selectedPanel = el("opsSelected");
const questionInput = el("opsQuestion");
const runButton = el("opsRun");
const briefPanel = el("opsBrief");
const evidencePanel = el("opsEvidence");
const evidenceCount = el("opsEvidenceCount");
const statusText = el("opsStatus");

function setStatus(message) {
  statusText.textContent = message;
}

// Column names are how the database spells a field, not how an operator reads
// one, so the card labels them in words.
function fieldLabel(column) {
  const words = column.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function text(value) {
  return value === null || value === undefined ? "—" : String(value);
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  return payload;
}

function renderProfiles() {
  profileSelect.innerHTML = "";
  PROFILES.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.label;
    profileSelect.append(option);
  });
  profileSelect.value = opsState.profile.id;
}

function recordLabel(record) {
  const parts = opsState.labelColumns
    .map((column) => record[column])
    .filter((value) => value !== null && value !== undefined);
  return parts.length ? parts.join(" · ") : `${text(record[opsState.keyColumn])}`;
}

function renderRecords() {
  recordList.innerHTML = "";
  if (!opsState.records.length) {
    recordList.innerHTML = '<p class="muted">No records match.</p>';
    recordCount.textContent = "";
    return;
  }
  recordCount.textContent = `${opsState.records.length}`;
  opsState.records.forEach((record) => {
    const id = record[opsState.keyColumn];
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ops-record";
    if (opsState.selected && String(opsState.selected) === String(id)) {
      button.classList.add("is-selected");
    }
    const title = document.createElement("span");
    title.className = "ops-record-title";
    title.textContent = recordLabel(record);
    const meta = document.createElement("span");
    meta.className = "ops-record-meta";
    meta.textContent = `${opsState.keyColumn} ${text(id)}`;
    button.append(title, meta);
    button.addEventListener("click", () => selectRecord(record));
    recordList.append(button);
  });
}

function renderSelected(record) {
  selectedPanel.classList.remove("empty");
  selectedPanel.innerHTML = "";

  const heading = document.createElement("h2");
  heading.className = "ops-selected-title";
  heading.textContent = recordLabel(record);
  selectedPanel.append(heading);

  const fields = document.createElement("div");
  fields.className = "ops-field-grid";
  Object.entries(record).forEach(([name, value]) => {
    const item = document.createElement("div");
    item.className = "ops-field";
    const label = document.createElement("span");
    label.className = "eyebrow";
    label.textContent = fieldLabel(name);
    const shown = document.createElement("span");
    shown.className = "ops-field-value";
    shown.textContent = text(value);
    item.append(label, shown);
    fields.append(item);
  });
  selectedPanel.append(fields);
}

function renderScope(scope) {
  const note = document.createElement("p");
  note.className = "ops-scope-note";
  note.textContent = `Scoped to ${scope.label} · every query filtered on ${Object.keys(
    scope.bindings,
  ).join(", ")}`;
  selectedPanel.append(note);
}

function renderBrief(payload) {
  briefPanel.innerHTML = "";

  const heading = document.createElement("div");
  heading.className = "ops-brief-head";
  heading.innerHTML = `<span class="eyebrow">Brief</span>`;
  briefPanel.append(heading);

  const body = document.createElement("p");
  body.className = "ops-brief-body";
  payload.brief.segments.forEach((segment) => {
    if (segment.type === "text") {
      body.append(document.createTextNode(segment.text));
      return;
    }
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ops-cite";
    chip.textContent = segment.evidence_id;
    chip.title = "Show the query behind this";
    chip.addEventListener("click", () => focusEvidence(segment.evidence_id));
    body.append(chip);
  });
  briefPanel.append(body);

  const issues = [...payload.validation_errors, ...payload.execution_errors];
  if (payload.blocked_query_count || issues.length) {
    const warning = document.createElement("div");
    warning.className = "ops-brief-warning";
    const blocked = payload.blocked_query_count
      ? `${payload.blocked_query_count} query blocked by access policy. `
      : "";
    warning.textContent = `${blocked}${issues.join(" ")}`.trim();
    briefPanel.append(warning);
  }
}

function renderEvidence(evidence) {
  opsState.evidence = evidence;
  evidencePanel.innerHTML = "";
  evidenceCount.textContent = evidence.length ? `${evidence.length} queries` : "None";
  if (!evidence.length) {
    evidencePanel.innerHTML = '<p class="muted">The agent gathered no evidence for this record.</p>';
    return;
  }

  evidence.forEach((item) => {
    const card = document.createElement("article");
    card.className = "ops-evidence";
    card.id = `evidence-${item.id}`;

    const head = document.createElement("div");
    head.className = "ops-evidence-head";
    const tag = document.createElement("span");
    tag.className = "ops-evidence-id";
    tag.textContent = item.id;
    const purpose = document.createElement("span");
    purpose.className = "ops-evidence-purpose";
    purpose.textContent = item.purpose || "query";
    head.append(tag, purpose);

    const sql = document.createElement("pre");
    sql.className = "ops-evidence-sql";
    sql.textContent = item.sql;

    const rows = document.createElement("div");
    rows.className = "ops-evidence-rows";
    rows.append(renderRows(item));

    card.append(head, sql, rows);
    evidencePanel.append(card);
  });
}

function renderRows(item) {
  if (!item.rows.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No rows.";
    return empty;
  }
  const table = document.createElement("table");
  table.className = "ops-table";
  const columns = Object.keys(item.rows[0]);

  const head = document.createElement("tr");
  columns.forEach((column) => {
    const cell = document.createElement("th");
    cell.textContent = column;
    head.append(cell);
  });
  table.append(head);

  item.rows.forEach((row) => {
    const line = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("td");
      cell.textContent = text(row[column]);
      line.append(cell);
    });
    table.append(line);
  });

  const wrap = document.createElement("div");
  wrap.append(table);
  if (item.truncated) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = `Showing ${item.rows.length} of ${item.row_count} rows.`;
    wrap.append(note);
  }
  return wrap;
}

function focusEvidence(evidenceId) {
  const card = document.getElementById(`evidence-${evidenceId}`);
  if (!card) return;
  document.querySelectorAll(".ops-evidence.is-focused").forEach((node) => {
    node.classList.remove("is-focused");
  });
  card.classList.add("is-focused");
  card.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function loadRecords() {
  recordList.innerHTML = '<p class="muted">Loading records…</p>';
  try {
    const payload = await postJson("/api/ops/records", {
      config_path: opsState.profile.configPath,
      search: searchInput.value,
    });
    opsState.records = payload.records;
    opsState.keyColumn = payload.key_column;
    opsState.labelColumns = payload.label_columns;
    opsState.defaultQuestion = payload.default_question;
    questionInput.placeholder = payload.default_question;
    renderRecords();
    setStatus(`${payload.table} · ${payload.database_id}`);
  } catch (error) {
    recordList.innerHTML = "";
    const message = document.createElement("p");
    message.className = "ops-error";
    message.textContent = error.message;
    recordList.append(message);
    setStatus("Could not load records");
  }
}

function selectRecord(record) {
  opsState.selected = record[opsState.keyColumn];
  renderRecords();
  renderSelected(record);
  runButton.disabled = false;
  briefPanel.innerHTML = '<p class="muted">Run an investigation to produce a brief.</p>';
  renderEvidence([]);
}

async function runInvestigation(event) {
  event.preventDefault();
  if (opsState.busy || opsState.selected === null) return;

  opsState.busy = true;
  runButton.disabled = true;
  runButton.textContent = "Investigating…";
  briefPanel.innerHTML = '<p class="muted">Running guarded queries against this record…</p>';

  try {
    const payload = await postJson("/api/ops/brief", {
      record_id: String(opsState.selected),
      config_path: opsState.profile.configPath,
      catalog_path: opsState.profile.catalogPath,
      schema_source: opsState.profile.schemaSource,
      question: questionInput.value.trim() || null,
    });
    renderScope(payload.scope);
    renderEvidence(payload.evidence);
    renderBrief(payload);
    setStatus(`Brief ready · ${payload.evidence.length} queries`);
  } catch (error) {
    briefPanel.innerHTML = "";
    const message = document.createElement("p");
    message.className = "ops-error";
    message.textContent = error.message;
    briefPanel.append(message);
    setStatus("Investigation failed");
  } finally {
    opsState.busy = false;
    runButton.disabled = false;
    runButton.textContent = "Investigate";
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* a blocked storage must not break the screen */
  }
}

let searchTimer = null;
searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(loadRecords, 250);
});

profileSelect.addEventListener("change", () => {
  opsState.profile =
    PROFILES.find((profile) => profile.id === profileSelect.value) || PROFILES[0];
  opsState.selected = null;
  runButton.disabled = true;
  selectedPanel.classList.add("empty");
  selectedPanel.innerHTML = '<p class="muted">No record selected.</p>';
  loadRecords();
});

el("opsForm").addEventListener("submit", runInvestigation);
el("toggleTheme").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

renderProfiles();
loadRecords();
