const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchWithTimeout(url, options = {}, timeoutMs = 2000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(id);
  }
}

async function waitForStatus(timeoutMs = 15000) {
  const loadingText = document.querySelector("#loading p");
  let attempts = 0;
  const deadline = Date.now() + timeoutMs;
  while (true) {
    attempts += 1;
    try {
      const url = new URL(withBase("/status"), window.location.origin);
      url.searchParams.set("_", Date.now().toString());
      const resp = await fetchWithTimeout(url, {}, 2000);
      if (resp.ok) return await resp.json();
    } catch (e) {}
    if (loadingText) {
      loadingText.textContent = attempts > 8 ? "Starting backend…" : "Loading…";
    }
    if (Date.now() >= deadline) return null;
    await sleep(500);
  }
}

function getBasePath() {
  const meta = document.querySelector('meta[name="app-base"]');
  return meta?.content || "";
}

function withBase(path) {
  const base = getBasePath();
  if (!base) return path;
  if (path.startsWith(base)) return path;
  return `${base}${path}`;
}

async function fetchJSON(url, options = {}) {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.error || "request_failed");
    err.data = data;
    throw err;
  }
  return data;
}

async function validateKey(key) {
  const body = { openai_api_key: key };
  const resp = await fetch(withBase("/validate_api_key"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || "validation_failed");
  }
  return data;
}

async function saveKey(key) {
  const body = { openai_api_key: key };
  const resp = await fetch(withBase("/openai_api_key"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "save_failed");
  }
  return await resp.json();
}

function rowTemplate(values = {}) {
  return {
    name: values.name || "",
    quantity: values.quantity ?? "",
    unit: values.unit || "",
    expiration_date: values.expiration_date || "",
    storage_location: values.storage_location || "",
  };
}

function renderInventoryTable(items) {
  const table = document.getElementById("inventory-table");
  if (!table) return;
  table.innerHTML = "";

  const header = document.createElement("div");
  header.className = "table-row header";
  header.innerHTML = "<div>Name</div><div>Qty</div><div>Unit</div><div>Expiration</div><div>Location</div>";
  table.appendChild(header);

  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "table-row";
    row.innerHTML = `
      <input data-field="name" value="${item.display_name || item.name || ""}" />
      <input data-field="quantity" type="number" step="0.01" value="${item.quantity ?? ""}" />
      <input data-field="unit" value="${item.unit || ""}" />
      <input data-field="expiration_date" placeholder="YYYY-MM-DD" value="${item.expiration_date || ""}" />
      <input data-field="storage_location" value="${item.storage_location || ""}" />
    `;
    table.appendChild(row);
  });
}

function collectInventoryRows() {
  const table = document.getElementById("inventory-table");
  if (!table) return [];
  const rows = Array.from(table.querySelectorAll(".table-row")).slice(1);
  return rows
    .map((row) => {
      const get = (field) => row.querySelector(`[data-field="${field}"]`)?.value?.trim() || "";
      const quantity = get("quantity");
      return {
        name: get("name"),
        quantity: quantity === "" ? 0 : Number(quantity),
        unit: get("unit"),
        expiration_date: get("expiration_date"),
        storage_location: get("storage_location"),
      };
    })
    .filter((item) => item.name);
}

function renderShoppingTable(items) {
  const table = document.getElementById("shopping-table");
  if (!table) return;
  table.innerHTML = "";
  const header = document.createElement("div");
  header.className = "table-row header";
  header.innerHTML = "<div>Name</div><div>Qty</div><div>Unit</div>";
  table.appendChild(header);

  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "table-row";
    row.innerHTML = `
      <input data-field="name" value="${item.name || ""}" />
      <input data-field="quantity" type="number" step="0.01" value="${item.quantity ?? ""}" />
      <input data-field="unit" value="${item.unit || ""}" />
    `;
    table.appendChild(row);
  });
}

function collectShoppingRows() {
  const table = document.getElementById("shopping-table");
  if (!table) return [];
  const rows = Array.from(table.querySelectorAll(".table-row")).slice(1);
  return rows
    .map((row) => {
      const get = (field) => row.querySelector(`[data-field="${field}"]`)?.value?.trim() || "";
      const quantity = get("quantity");
      return {
        name: get("name"),
        quantity: quantity === "" ? 0 : Number(quantity),
        unit: get("unit") || "pcs",
      };
    })
    .filter((item) => item.name);
}

function renderMealPlan(plan) {
  const container = document.getElementById("meal-plan-content");
  if (!container) return;
  container.innerHTML = "";
  if (!plan || !plan.days) {
    container.innerHTML = "<p class=\"muted\">No plan yet.</p>";
    return;
  }
  plan.days.forEach((day) => {
    const card = document.createElement("div");
    card.className = "card";
    const meals = (day.meals || [])
      .map((meal) => `<li><strong>${meal.meal}</strong>: ${meal.name}</li>`)
      .join("");
    card.innerHTML = `
      <h3>${day.day} <span>${day.date || ""}</span></h3>
      <ul>${meals || "<li>No meals planned.</li>"}</ul>
    `;
    container.appendChild(card);
  });
}

function show(el, flag) {
  el.classList.toggle("hidden", !flag);
}

async function init() {
  const loading = document.getElementById("loading");
  const statusEl = document.getElementById("status");
  const formPanel = document.getElementById("form-panel");
  const configuredPanel = document.getElementById("configured");
  const saveBtn = document.getElementById("save-btn");
  const changeKeyBtn = document.getElementById("change-key-btn");
  const input = document.getElementById("api-key");
  const commandBtn = document.getElementById("send-command-btn");
  const commandText = document.getElementById("command-text");
  const commandStatus = document.getElementById("command-status");
  const transcriptBox = document.getElementById("transcript");
  const languageSelect = document.getElementById("language-select");
  const languageCustom = document.getElementById("language-custom");
  const saveLanguageBtn = document.getElementById("save-language-btn");
  const languageStatus = document.getElementById("language-status");
  const saveProfileBtn = document.getElementById("save-profile-btn");
  const profileStatus = document.getElementById("profile-status");
  const adultsInput = document.getElementById("adults");
  const childrenInput = document.getElementById("children");
  const addInventoryRow = document.getElementById("add-inventory-row");
  const saveInventoryBtn = document.getElementById("save-inventory-btn");
  const inventoryStatus = document.getElementById("inventory-status");
  const generatePlanBtn = document.getElementById("generate-plan-btn");
  const mealPlanStatus = document.getElementById("meal-plan-status");
  const generateShoppingBtn = document.getElementById("generate-shopping-btn");
  const saveShoppingBtn = document.getElementById("save-shopping-btn");
  const shoppingStatus = document.getElementById("shopping-status");

  show(loading, true);
  show(formPanel, false);
  show(configuredPanel, false);

  const st = (await waitForStatus()) || { has_key: false };

  if (st.has_key) {
    show(configuredPanel, true);
  } else {
    show(formPanel, true);
  }
  show(loading, false);

  try {
    const profile = await fetchJSON(withBase("/data/family_profile"));
    if (profile.profile) {
      adultsInput.value = profile.profile.adults ?? "";
      childrenInput.value = profile.profile.children ?? "";
    }
  } catch (e) {}

  try {
    const inventory = await fetchJSON(withBase("/data/inventory"));
    renderInventoryTable(inventory.items || []);
  } catch (e) {
    renderInventoryTable([]);
  }

  try {
    const plan = await fetchJSON(withBase("/data/meal_plan"));
    renderMealPlan(plan.plan || null);
  } catch (e) {
    renderMealPlan(null);
  }

  try {
    const shopping = await fetchJSON(withBase("/data/shopping_list"));
    renderShoppingTable(shopping.items || []);
  } catch (e) {
    renderShoppingTable([]);
  }

  try {
    const settings = await fetchJSON(withBase("/settings"));
    const language = settings.settings?.language || "en";
    if (languageSelect) {
      const option = Array.from(languageSelect.options).find((opt) => opt.value === language);
      if (option) {
        languageSelect.value = language;
        languageCustom.value = "";
      } else {
        languageSelect.value = "en";
        languageCustom.value = language;
      }
    }
  } catch (e) {}

  changeKeyBtn.addEventListener("click", () => {
    show(configuredPanel, false);
    show(formPanel, true);
    input.value = "";
    statusEl.textContent = "";
    statusEl.className = "status";
  });

  input.addEventListener("input", () => {
    input.classList.remove("error");
  });

  saveBtn.addEventListener("click", async () => {
    const key = input.value.trim();
    if (!key) {
      statusEl.textContent = "Please enter a valid key.";
      statusEl.className = "status warn";
      input.classList.add("error");
      return;
    }
    statusEl.textContent = "Validating API key...";
    statusEl.className = "status";
    input.classList.remove("error");
    try {
      const validation = await validateKey(key);
      if (!validation.valid) {
        statusEl.textContent = "Invalid API key. Please check your key and try again.";
        statusEl.className = "status error";
        input.classList.add("error");
        return;
      }
      statusEl.textContent = "Key valid! Saving...";
      statusEl.className = "status ok";
      await saveKey(key);
      statusEl.textContent = "Saved. Reloading…";
      statusEl.className = "status ok";
      window.location.reload();
    } catch (e) {
      input.classList.add("error");
      if (e.message === "invalid_api_key") {
        statusEl.textContent = "Invalid API key. Please check your key and try again.";
      } else {
        statusEl.textContent = "Failed to validate/save key. Please try again.";
      }
      statusEl.className = "status error";
    }
  });

  async function refreshTranscript() {
    if (!transcriptBox) return;
    try {
      const data = await fetchJSON("/transcript");
      const messages = data.messages || [];
      const escapeHtml = (text) =>
        text
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");
      const avatarFor = (role) => {
        const normalized = (role || "assistant").toLowerCase();
        if (normalized.startsWith("user")) return "static/user_avatar.png";
        return "static/reachymini_avatar.png";
      };
      transcriptBox.innerHTML = messages
        .map((msg) => {
          const role = (msg.role || "assistant").toLowerCase().startsWith("user")
            ? "user"
            : "assistant";
          const avatar = avatarFor(msg.role);
          const content = escapeHtml(msg.content || "");
          return `
            <div class="msg ${role}">
              <img class="avatar" src="${avatar}" alt="${role}" />
              <div class="bubble ${role}">${content}</div>
            </div>
          `;
        })
        .join("");
    } catch (e) {
      // ignore
    }
  }

  setInterval(refreshTranscript, 1500);
  refreshTranscript();

  commandBtn?.addEventListener("click", async () => {
    const text = commandText.value.trim();
    if (!text) return;
    commandStatus.textContent = "Sending...";
    try {
      await fetchJSON(withBase("/text_input"), { method: "POST", body: JSON.stringify({ text }) });
      commandStatus.textContent = "Sent.";
      commandText.value = "";
    } catch (e) {
      commandStatus.textContent = "Failed to send.";
    }
  });

  saveLanguageBtn?.addEventListener("click", async () => {
    const preset = languageSelect?.value || "en";
    const custom = (languageCustom?.value || "").trim();
    const language = custom || preset;
    if (languageStatus) languageStatus.textContent = "Saving...";
    try {
      await fetchJSON(withBase("/settings"), {
        method: "POST",
        body: JSON.stringify({ language }),
      });
      if (languageStatus) languageStatus.textContent = `Language set to ${language}.`;
    } catch (e) {
      if (languageStatus) languageStatus.textContent = "Failed to save language.";
    }
  });

  saveProfileBtn?.addEventListener("click", async () => {
    profileStatus.textContent = "Saving...";
    try {
      const adults = adultsInput.value === "" ? null : Number(adultsInput.value);
      const children = childrenInput.value === "" ? null : Number(childrenInput.value);
      await fetchJSON(withBase("/data/family_profile"), {
        method: "POST",
        body: JSON.stringify({ adults, children }),
      });
      profileStatus.textContent = "Saved.";
    } catch (e) {
      profileStatus.textContent = "Failed to save.";
    }
  });

  addInventoryRow?.addEventListener("click", () => {
    const items = collectInventoryRows();
    items.push(rowTemplate());
    renderInventoryTable(items);
  });

  saveInventoryBtn?.addEventListener("click", async () => {
    inventoryStatus.textContent = "Saving...";
    try {
      const items = collectInventoryRows();
      await fetchJSON(withBase("/data/inventory/replace"), {
        method: "POST",
        body: JSON.stringify({ items }),
      });
      inventoryStatus.textContent = "Saved.";
    } catch (e) {
      inventoryStatus.textContent = "Failed to save.";
    }
  });

  generatePlanBtn?.addEventListener("click", async () => {
    mealPlanStatus.textContent = "Generating...";
    try {
      const plan = await fetchJSON(withBase("/data/meal_plan/generate"), { method: "POST" });
      renderMealPlan(plan.plan || null);
      mealPlanStatus.textContent = "Plan updated.";
    } catch (e) {
      mealPlanStatus.textContent = "Failed to generate.";
    }
  });

  generateShoppingBtn?.addEventListener("click", async () => {
    shoppingStatus.textContent = "Generating...";
    try {
      const shopping = await fetchJSON(withBase("/data/shopping_list/generate"), { method: "POST" });
      renderShoppingTable(shopping.items || []);
      shoppingStatus.textContent = "Shopping list updated.";
    } catch (e) {
      shoppingStatus.textContent = "Failed to generate.";
    }
  });

  saveShoppingBtn?.addEventListener("click", async () => {
    shoppingStatus.textContent = "Saving...";
    try {
      const items = collectShoppingRows();
      await fetchJSON(withBase("/data/shopping_list/replace"), {
        method: "POST",
        body: JSON.stringify({ items }),
      });
      shoppingStatus.textContent = "Saved.";
    } catch (e) {
      shoppingStatus.textContent = "Failed to save.";
    }
  });
}

window.addEventListener("DOMContentLoaded", init);
