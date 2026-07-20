const state = {
  sessionId: null,
  busy: false,
  steps: [],
};

const THEME_STORAGE_KEY = "db-agent-theme";
const DATA_PROFILES = [
  {
    id: "bank:learned",
    label: "Bank Config · Learned catalog",
    configPath: "config/bank.example.yaml",
    catalogPath: "config/bank_catalog.json",
    schemaSource: "learned",
    catalogReady: true,
  },
  {
    id: "bank:runtime",
    label: "Bank Config · Live schema",
    configPath: "config/bank.example.yaml",
    catalogPath: "config/bank_catalog.json",
    schemaSource: "runtime",
    catalogReady: true,
  },
  {
    id: "murder:learned",
    label: "Murder Mystery Config · Learned catalog",
    configPath: "config/murder_mystery.example.yaml",
    catalogPath: "config/murder_mystery_catalog.json",
    schemaSource: "learned",
    catalogReady: true,
  },
  {
    id: "murder:runtime",
    label: "Murder Mystery Config · Live schema",
    configPath: "config/murder_mystery.example.yaml",
    catalogPath: "config/murder_mystery_catalog.json",
    schemaSource: "runtime",
    catalogReady: true,
  },
  {
    id: "generic:runtime",
    label: "Generic Example Config · Live schema",
    configPath: "config/databases.example.yaml",
    catalogPath: "config/databases_catalog.json",
    schemaSource: "runtime",
    catalogReady: false,
  },
  {
    id: "fineract:learned",
    label: "Fineract Core Banking · Learned catalog",
    configPath: "config/fineract.example.yaml",
    catalogPath: "config/fineract_catalog.json",
    schemaSource: "learned",
    catalogReady: true,
  },
];
const themePreference = window.matchMedia?.("(prefers-color-scheme: dark)") || {
  matches: false,
  addEventListener: () => {},
};

const elements = {
  shell: document.getElementById("shell"),
  toggleSidebar: document.getElementById("toggleSidebar"),
  toggleTrace: document.getElementById("toggleTrace"),
  toggleTheme: document.getElementById("toggleTheme"),
  statusText: document.getElementById("statusText"),
  profileSelect: document.getElementById("profileSelect"),
  profileDetails: document.getElementById("profileDetails"),
  databaseSelect: document.getElementById("databaseSelect"),
  indexButton: document.getElementById("indexButton"),
  resetButton: document.getElementById("resetButton"),
  chatForm: document.getElementById("chatForm"),
  messageInput: document.getElementById("messageInput"),
  sendButton: document.getElementById("sendButton"),
  messages: document.getElementById("messages"),
  chatScroll: document.querySelector(".chat-scroll"),
  selectedDatabases: document.getElementById("selectedDatabases"),
  trace: document.getElementById("trace"),
  sessionBadge: document.getElementById("sessionBadge"),
};

const ROLE_LABELS = {
  user: "You",
  assistant: "Agent",
  system: "Session",
  error: "Error",
};

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Request failed");
  }
  return data;
}

function addMessage(role, text) {
  const node = document.createElement("div");
  node.className = `message role-${role}`;

  const body = document.createElement("div");
  body.className = `message-text role-${role}`;
  // Agent replies come back as markdown; render them. User input and system
  // notices stay literal text.
  if (role === "assistant") {
    body.classList.add("markdown");
    body.innerHTML = renderMarkdown(text);
  } else {
    body.textContent = text;
  }

  // User messages render as a right-aligned bubble with no label; every
  // other role keeps its uppercase label above the text.
  if (role !== "user") {
    const label = document.createElement("div");
    label.className = `message-label role-${role}`;
    label.textContent = ROLE_LABELS[role] || role;
    node.appendChild(label);
  }

  node.appendChild(body);
  elements.messages.appendChild(node);
  elements.chatScroll.scrollTop = elements.chatScroll.scrollHeight;
}

function setBusy(busy) {
  state.busy = busy;
  elements.sendButton.disabled = busy;
  elements.indexButton.disabled = busy;
  elements.messageInput.disabled = busy;
  elements.profileSelect.disabled = busy;
  elements.databaseSelect.disabled = busy;
}

function selectedProfile() {
  return DATA_PROFILES.find((profile) => profile.id === elements.profileSelect.value) || DATA_PROFILES[0];
}

function settingsPayload() {
  const profile = selectedProfile();
  const selectedDatabaseId = elements.databaseSelect.value;
  return {
    config_path: profile.configPath,
    catalog_path: profile.catalogPath || null,
    schema_source: profile.schemaSource,
    selected_database_ids: selectedDatabaseId ? [selectedDatabaseId] : [],
  };
}

function traceSection(label, innerHtml) {
  return `<div class="trace-section"><span class="eyebrow">${escapeHtml(label)}</span>${innerHtml}</div>`;
}

function renderTrace(data) {
  const selected = data.selected_databases || [];
  elements.selectedDatabases.className = selected.length ? "pill-row" : "pill-row muted";
  elements.selectedDatabases.innerHTML = selected.length
    ? selected
        .map(
          (db) =>
            `<span class="pill"><span class="pill-dot"></span>${escapeHtml(db.id)} · ${escapeHtml(db.score)}</span>`
        )
        .join("")
    : "No database selected";

  const sections = [];
  sections.push(
    traceSection(
      "Standalone Question",
      `<div class="trace-box">${escapeHtml(data.standalone_question || "")}</div>`
    )
  );

  if (data.sql_plans?.length) {
    sections.push(
      traceSection(
        "SQL Plans",
        `<pre class="trace-code">${escapeHtml(JSON.stringify(data.sql_plans, null, 2))}</pre>`
      )
    );
  }

  if (data.validation_errors?.length) {
    sections.push(
      traceSection(
        "Validation",
        `<pre class="trace-alert">${escapeHtml(data.validation_errors.join("\n"))}</pre>`
      )
    );
  }

  if (data.query_results?.length) {
    sections.push(
      traceSection(
        "Result Preview",
        `<pre class="trace-code">${escapeHtml(JSON.stringify(data.query_results, null, 2))}</pre>`
      )
    );
  }

  elements.trace.innerHTML = sections.join("");
}

function renderProgress(steps) {
  const body = steps
    .map((step) => {
      const symbol = step.status === "complete" ? "✓" : step.status === "error" ? "!" : "···";
      const detail = step.detail ? `<div class="step-detail">${escapeHtml(step.detail)}</div>` : "";
      return `<div class="step ${escapeHtml(step.status || "running")}">
        <span class="step-icon">${symbol}</span>
        <div><div class="step-title">${escapeHtml(step.title)}</div>${detail}</div>
      </div>`;
    })
    .join("");
  elements.trace.innerHTML = traceSection(
    "Agent Steps",
    `<div class="step-list">${body || '<div class="step-detail" style="padding:12px 15px;">Starting</div>'}</div>`
  );
}

function renderMarkdown(text) {
  const raw = String(text ?? "");
  if (window.marked && window.DOMPurify) {
    const html = window.marked.parse(raw, { gfm: true, breaks: true });
    return window.DOMPurify.sanitize(html);
  }
  // Fallback if the markdown libraries did not load: escaped plain text.
  return escapeHtml(raw).replaceAll("\n", "<br>");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function autoGrow() {
  elements.messageInput.style.height = "auto";
  elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 140)}px`;
}

function storedTheme() {
  try {
    const theme = localStorage.getItem(THEME_STORAGE_KEY);
    return theme === "dark" || theme === "light" ? theme : null;
  } catch {
    return null;
  }
}

function setTheme(theme, persist = true) {
  document.documentElement.dataset.theme = theme;
  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // The visual theme can still change even if storage is unavailable.
    }
  }

  const nextTheme = theme === "dark" ? "light" : "dark";
  elements.toggleTheme.title = `Switch to ${nextTheme} mode`;
  elements.toggleTheme.setAttribute("aria-label", `Switch to ${nextTheme} mode`);
}

function syncTheme() {
  const savedTheme = storedTheme();
  setTheme(savedTheme || (themePreference.matches ? "dark" : "light"), Boolean(savedTheme));
}

function initProfiles() {
  elements.profileSelect.innerHTML = DATA_PROFILES.map((profile) => {
    const suffix = profile.schemaSource === "learned" && !profile.catalogReady ? " (catalog missing)" : "";
    return `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.label)}${escapeHtml(suffix)}</option>`;
  }).join("");
  elements.profileSelect.value = DATA_PROFILES[0].id;
  renderProfileDetails();
}

function renderProfileDetails() {
  const profile = selectedProfile();
  const catalogStatus = profile.catalogReady ? "catalog ready" : "catalog missing";
  const schemaLabel = profile.schemaSource === "runtime" ? "live schema" : "learned catalog";
  elements.profileDetails.textContent =
    `${profile.configPath} · ${profile.catalogPath} · ${schemaLabel} · ${catalogStatus}`;
}

async function refreshStatus() {
  const response = await fetch("/api/status");
  const data = await response.json();
  elements.statusText.textContent = data.model_configured
    ? `${data.provider} · ${data.model}`
    : `Set ${data.provider === "gemini" ? "GOOGLE_API_KEY" : "OPENAI_API_KEY"} to answer`;
}

async function refreshDatabases({ preserveSelection = true } = {}) {
  const currentValue = preserveSelection ? elements.databaseSelect.value : "";
  const profile = selectedProfile();
  try {
    const data = await api("/api/databases", {
      config_path: profile.configPath,
      catalog_path: profile.catalogPath || null,
    });
    const options = ['<option value="">Auto-route</option>'];
    for (const database of data.databases || []) {
      const tableText = database.tables === null ? "" : ` (${database.tables} tables)`;
      options.push(
        `<option value="${escapeHtml(database.id)}">${escapeHtml(database.name)} · ${escapeHtml(database.id)}${escapeHtml(tableText)}</option>`
      );
    }
    elements.databaseSelect.innerHTML = options.join("");
    if ([...elements.databaseSelect.options].some((option) => option.value === currentValue)) {
      elements.databaseSelect.value = currentValue;
    }
  } catch (error) {
    elements.databaseSelect.innerHTML = '<option value="">Auto-route</option>';
    addMessage("error", `Could not load databases: ${error.message}`);
  }
}

elements.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message || state.busy) return;

  addMessage("user", message);
  elements.messageInput.value = "";
  autoGrow();
  setBusy(true);
  state.steps = [];
  renderProgress([{ title: "Starting", status: "running" }]);

  try {
    const data = await streamChat({
      ...settingsPayload(),
      session_id: state.sessionId,
      message,
    });
    state.sessionId = data.session_id;
    elements.sessionBadge.textContent = `Session ${state.sessionId.slice(0, 8)}`;
    addMessage("assistant", data.answer);
    renderTrace(data);
  } catch (error) {
    addMessage("error", error.message);
  } finally {
    setBusy(false);
    elements.messageInput.focus();
  }
});

elements.messageInput.addEventListener("input", autoGrow);
elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});

async function streamChat(payload) {
  const response = await fetch("/api/chat-stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Request failed");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === "step") {
        state.steps.push(event);
        renderProgress(state.steps);
      } else if (event.type === "result") {
        finalResult = event.data;
      } else if (event.type === "error") {
        throw new Error(event.message);
      }
    }
  }

  if (!finalResult) {
    throw new Error("No result produced");
  }
  return finalResult;
}

elements.indexButton.addEventListener("click", async () => {
  if (state.busy) return;
  const profile = selectedProfile();
  setBusy(true);
  try {
    const data = await api("/api/index", {
      config_path: profile.configPath,
      output_path: profile.catalogPath,
    });
    addMessage("system", `${data.message} Catalog: ${data.output_path}`);
    profile.catalogReady = true;
    renderProfileDetails();
    await refreshStatus();
    await refreshDatabases();
  } catch (error) {
    addMessage("error", error.message);
  } finally {
    setBusy(false);
  }
});

elements.profileSelect.addEventListener("change", () => {
  elements.databaseSelect.value = "";
  renderProfileDetails();
  refreshDatabases({ preserveSelection: false });
});

elements.resetButton.addEventListener("click", async () => {
  if (state.sessionId) {
    await api("/api/reset", { session_id: state.sessionId }).catch(() => {});
  }
  state.sessionId = null;
  elements.sessionBadge.textContent = "New session";
  elements.messages.innerHTML = "";
  elements.trace.innerHTML =
    '<p class="muted">Generated SQL, validation errors, and result previews appear here.</p>';
  elements.selectedDatabases.className = "pill-row muted";
  elements.selectedDatabases.textContent = "No query yet";
});

elements.toggleSidebar.addEventListener("click", () => {
  elements.shell.classList.toggle("sidebar-collapsed");
});

elements.toggleTrace.addEventListener("click", () => {
  elements.shell.classList.toggle("trace-collapsed");
});

elements.toggleTheme.addEventListener("click", () => {
  const currentTheme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  setTheme(currentTheme === "dark" ? "light" : "dark");
});

themePreference.addEventListener("change", () => {
  if (!storedTheme()) {
    syncTheme();
  }
});

syncTheme();
initProfiles();
refreshStatus();
refreshDatabases();
addMessage(
  "system",
  "Choose a data profile, index a catalog if needed, then ask a database question. Follow-ups will use this chat session as context."
);
