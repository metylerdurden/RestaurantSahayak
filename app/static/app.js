"use strict";

const state = {
  restaurantId: localStorage.getItem("dineops.restaurantId") || null,
  managerUserId: null,
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function fmtTime(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString();
}

function badge(text) {
  const cls = String(text || "").toLowerCase();
  return `<span class="badge ${esc(cls)}">${esc(text)}</span>`;
}

async function api(path, options) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options && options.headers) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {
      /* no JSON body */
    }
    throw new Error(`${response.status}: ${detail}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function withRestaurant(path) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("restaurant_id", state.restaurantId);
  return url.pathname + url.search;
}

// --- navigation ---

function switchView(name) {
  document.querySelectorAll("section.view").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll("nav button").forEach((el) => el.classList.remove("active"));
  document.getElementById(`view-${name}`).classList.add("active");
  document.querySelector(`nav button[data-view="${name}"]`).classList.add("active");
  loadView(name);
}

function loadView(name) {
  if (!state.restaurantId) return;
  const loaders = {
    dashboard: loadDashboard,
    reservations: loadReservations,
    inventory: loadInventory,
    customers: loadCustomers,
    approvals: loadApprovals,
    "agent-activity": loadAgentRuns,
    workflows: () => {},
  };
  (loaders[name] || (() => {}))();
}

// --- restaurant picker ---

async function loadRestaurants() {
  const restaurants = await api("/api/v1/restaurants");
  const select = document.getElementById("restaurant-select");
  select.innerHTML = restaurants.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join("");
  if (!state.restaurantId || !restaurants.some((r) => r.id === state.restaurantId)) {
    state.restaurantId = restaurants[0] ? restaurants[0].id : null;
  }
  if (state.restaurantId) select.value = state.restaurantId;
  select.addEventListener("change", () => {
    state.restaurantId = select.value;
    localStorage.setItem("dineops.restaurantId", state.restaurantId);
    refreshCurrentView();
  });
}

function refreshCurrentView() {
  const active = document.querySelector("nav button.active");
  loadView(active ? active.dataset.view : "dashboard");
}

// --- dashboard ---

async function loadDashboard() {
  const data = await api(withRestaurant("/api/v1/dashboard"));
  state.managerUserId = data.manager_user_id;
  document.getElementById("manager-info").textContent = data.manager_user_id
    ? `manager: ${data.manager_user_id.slice(0, 8)}…`
    : "";

  document.getElementById("dash-covers").textContent = data.expected_covers;

  document.getElementById("dash-reservations").innerHTML = data.today_reservations.length
    ? `<table><tr><th>Time</th><th>Party</th><th>Status</th></tr>${data.today_reservations
        .map((r) => `<tr><td>${fmtTime(r.requested_time)}</td><td>${r.party_size}</td><td>${badge(r.status)}</td></tr>`)
        .join("")}</table>`
    : `<p class="empty">No reservations today.</p>`;

  document.getElementById("dash-inventory").innerHTML = data.inventory_alerts.length
    ? data.inventory_alerts.map((i) => `<div>${esc(i.name)} — ${i.quantity_on_hand} ${esc(i.unit)} ${badge(i.status)}</div>`).join("")
    : `<p class="empty">No inventory alerts.</p>`;

  document.getElementById("dash-staffing").innerHTML = data.staffing_alerts.length
    ? data.staffing_alerts
        .map((s) => `<div>${fmtTime(s.start_at)} – ${fmtTime(s.end_at)}: ${s.assignments.length}/${s.required_staff_count} staffed ${badge(s.status)}</div>`)
        .join("")
    : `<p class="empty">All shifts staffed.</p>`;

  document.getElementById("dash-approvals").innerHTML = data.pending_approvals.length
    ? data.pending_approvals.map((a) => `<div>${esc(a.reason)} ${badge(a.risk_level)}</div>`).join("")
    : `<p class="empty">Nothing pending.</p>`;

  document.getElementById("dash-activity").innerHTML = data.recent_agent_activity.length
    ? data.recent_agent_activity
        .map((r) => `<div>${esc(r.agent_name)} ${badge(r.status)} <span class="muted">${fmtTime(r.started_at)}</span></div>`)
        .join("")
    : `<p class="empty">No agent activity yet.</p>`;

  document.getElementById("dash-briefing").innerHTML = data.daily_briefing
    ? `<p>${esc(data.daily_briefing)}</p><p class="muted">Generated ${fmtTime(data.daily_briefing_generated_at)}</p>`
    : `<p class="empty">No briefing yet — trigger one from the Workflows tab.</p>`;

  document.getElementById("dash-events").innerHTML = data.recent_events.length
    ? `<table><tr><th>Event</th><th>When</th><th>Handled</th></tr>${data.recent_events
        .map((e) => `<tr><td>${esc(e.event_type)}</td><td>${fmtTime(e.created_at)}</td><td>${e.handled ? "yes" : "no"}</td></tr>`)
        .join("")}</table>`
    : `<p class="empty">No recent events.</p>`;
}

// --- reservations ---

async function loadReservations() {
  const reservations = await api(withRestaurant("/api/v1/reservations"));
  const rows = reservations
    .map(
      (r) => `<tr>
        <td>${fmtTime(r.requested_time)}</td>
        <td>${r.party_size}</td>
        <td>${badge(r.status)}</td>
        <td>${esc(r.notes || "")}</td>
        <td class="row-actions">
          <button class="action secondary" onclick="editReservation('${r.id}', ${r.party_size})">Edit size</button>
          <button class="action danger" onclick="cancelReservation('${r.id}')">Cancel</button>
        </td>
      </tr>`
    )
    .join("");
  document.getElementById("reservations-table").innerHTML = reservations.length
    ? `<table><tr><th>Time</th><th>Party</th><th>Status</th><th>Notes</th><th></th></tr>${rows}</table>`
    : `<p class="empty">No reservations found.</p>`;
}

async function editReservation(id, currentSize) {
  const value = window.prompt("New party size:", currentSize);
  if (!value) return;
  const result = await api(`/api/v1/reservations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({
      restaurant_id: state.restaurantId,
      party_size: Number(value),
      initiated_by_user_id: state.managerUserId,
    }),
  });
  reportMutation(result);
  loadReservations();
}

async function cancelReservation(id) {
  const reason = window.prompt("Cancellation reason (optional):", "") || "";
  let url = withRestaurant(`/api/v1/reservations/${id}`) + `&reason=${encodeURIComponent(reason)}`;
  if (state.managerUserId) url += `&initiated_by_user_id=${state.managerUserId}`;
  const result = await api(url, { method: "DELETE" });
  reportMutation(result);
  loadReservations();
}

function reportMutation(result) {
  if (result.status === "pending_approval") {
    alert(`Sent for manager approval: ${result.summary}`);
  }
}

document.getElementById("create-reservation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const body = {
    restaurant_id: state.restaurantId,
    customer_id: form.get("customer_id"),
    party_size: Number(form.get("party_size")),
    requested_time: new Date(form.get("requested_time")).toISOString(),
    table_id: form.get("table_id") || null,
    notes: form.get("notes") || null,
    initiated_by_user_id: state.managerUserId,
  };
  try {
    const result = await api("/api/v1/reservations", { method: "POST", body: JSON.stringify(body) });
    reportMutation(result);
    event.target.reset();
    loadReservations();
  } catch (err) {
    alert(err.message);
  }
});

// --- inventory ---

async function loadInventory() {
  const items = await api(withRestaurant("/api/v1/inventory"));
  document.getElementById("inventory-table").innerHTML = items.length
    ? `<table><tr><th>Item</th><th>On hand</th><th>Threshold</th><th>Status</th></tr>${items
        .map((i) => `<tr><td>${esc(i.name)}</td><td>${i.quantity_on_hand} ${esc(i.unit)}</td><td>${i.low_stock_threshold}</td><td>${badge(i.status)}</td></tr>`)
        .join("")}</table>`
    : `<p class="empty">No inventory items.</p>`;
}

// --- customers ---

async function loadCustomers() {
  const customers = await api(withRestaurant("/api/v1/customers"));
  document.getElementById("customers-list").innerHTML = customers.length
    ? `<table><tr><th>Name</th><th>Phone</th><th>Email</th><th></th></tr>${customers
        .map(
          (c) => `<tr><td>${esc(c.name)}</td><td>${esc(c.phone || "")}</td><td>${esc(c.email || "")}</td>
            <td><button class="action secondary" onclick="selectCustomer('${c.id}')">View memories</button></td></tr>`
        )
        .join("")}</table>`
    : `<p class="empty">No customers found.</p>`;
}

async function selectCustomer(id) {
  const memories = await api(withRestaurant(`/api/v1/customers/${id}/memories`));
  document.getElementById("customer-detail").innerHTML = `
    <h3>Memories</h3>
    ${
      memories.length
        ? memories
            .map(
              (m) => `<div class="trace-message memory"><strong>${esc(m.topic)}</strong> (${esc(m.memory_type)}, importance ${m.importance}) — ${esc(m.content.text || JSON.stringify(m.content))}</div>`
            )
            .join("")
        : `<p class="empty">Nothing remembered about this customer yet.</p>`
    }`;
}

// --- approvals ---

async function loadApprovals() {
  const approvals = await api(withRestaurant("/api/v1/approvals"));
  document.getElementById("approvals-list").innerHTML = approvals.length
    ? approvals
        .map(
          (a) => `<div class="card" style="margin-bottom:0.6rem">
            <div class="section-title"><strong>${esc(a.action)}</strong> ${badge(a.risk_level)}</div>
            <p>${esc(a.reason)}</p>
            <p class="muted">Proposed by ${esc(a.agent_name)} &middot; ${fmtTime(a.created_at)}</p>
            <div class="row-actions">
              <button class="action" onclick="decideApproval('${a.id}', 'approve')">Approve</button>
              <button class="action danger" onclick="decideApproval('${a.id}', 'reject')">Reject</button>
            </div>
          </div>`
        )
        .join("")
    : `<p class="empty">Nothing pending approval.</p>`;
}

async function decideApproval(id, decision) {
  if (!state.managerUserId) {
    await loadDashboard(); // makes sure manager_user_id is populated
  }
  await api(`/api/v1/approvals/${id}/${decision}`, {
    method: "POST",
    body: JSON.stringify({ decided_by_user_id: state.managerUserId }),
  });
  loadApprovals();
}

// --- agent activity ---

document.getElementById("trigger-agent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const resultEl = document.getElementById("trigger-result");
  resultEl.textContent = "Running — this calls the real model and can take a while…";
  try {
    const result = await api("/api/v1/agent-runs", {
      method: "POST",
      body: JSON.stringify({
        restaurant_id: state.restaurantId,
        agent_name: form.get("agent_name"),
        task: form.get("task"),
        initiated_by_user_id: state.managerUserId,
      }),
    });
    resultEl.textContent = `${result.status}: ${result.summary}`;
    loadAgentRuns();
    loadTrace(result.agent_run_id);
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
  }
});

async function loadAgentRuns() {
  const runs = await api(withRestaurant("/api/v1/agent-runs") + "&limit=15");
  document.getElementById("agent-runs-list").innerHTML = runs.length
    ? runs
        .map(
          (r) => `<div class="trace-node" style="cursor:pointer" onclick="loadTrace('${r.id}')">
            <strong>${esc(r.agent_name)}</strong> ${badge(r.status)}<br/>
            <span class="muted">${fmtTime(r.started_at)}</span>
            ${r.outcome_summary ? `<p>${esc(r.outcome_summary)}</p>` : ""}
          </div>`
        )
        .join("")
    : `<p class="empty">No agent runs yet — trigger one above.</p>`;
}

const MEMORY_TOOLS = new Set(["search_memory", "add_memory", "get_memory", "update_memory", "reinforce_memory", "forget_memory", "delete_memory"]);

function renderTraceNode(node, depth) {
  const messages = node.messages
    .filter((m) => m.role === "tool_result")
    .map((m) => {
      const isMemory = m.tool_name && MEMORY_TOOLS.has(m.tool_name);
      const label = isMemory ? `Memory: ${m.tool_name}` : `Tool: ${m.tool_name || "?"}`;
      return `<div class="trace-arrow">↓</div><div class="trace-message ${isMemory ? "memory" : "tool"}">${esc(label)}</div>`;
    })
    .join("");

  const childrenHtml = node.children.length
    ? `<div class="trace-arrow">↓</div><div class="trace-children">${node.children
        .map((c) => renderTraceNode(c, depth + 1))
        .join('<div class="trace-arrow">↓</div>')}</div>`
    : "";

  const label = depth === 0 ? (node.children.length ? "Orchestrator" : node.agent_name) : "Specialist Agent";

  return `
    <div class="trace-node">
      <strong>${esc(label)}</strong> (${esc(node.agent_name)}) ${badge(node.status)}
    </div>
    ${messages}
    ${childrenHtml}
    <div class="trace-arrow">↓</div>
    <div class="trace-message">Result: ${esc(node.outcome_summary || "(no summary)")}</div>
  `;
}

async function loadTrace(runId) {
  const node = await api(`/api/v1/agent-runs/${runId}/trace`);
  document.getElementById("agent-run-trace").innerHTML = `
    <div class="trace-node"><strong>Manager Request</strong></div>
    <div class="trace-arrow">↓</div>
    ${renderTraceNode(node, 0)}
  `;
}

// --- workflows ---

document.querySelectorAll("#view-workflows button[data-workflow]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const resultEl = document.getElementById("workflow-result");
    resultEl.textContent = "Running…";
    try {
      const result = await api(withRestaurant(`/api/v1/workflows/${btn.dataset.workflow}`), { method: "POST" });
      resultEl.textContent = JSON.stringify(result, null, 2);
      if (btn.dataset.workflow === "daily-briefing") loadDashboard();
    } catch (err) {
      resultEl.textContent = `Error: ${err.message}`;
    }
  });
});

// --- boot ---

document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});
document.getElementById("refresh-btn").addEventListener("click", refreshCurrentView);

(async function init() {
  try {
    await loadRestaurants();
    if (state.restaurantId) loadView("dashboard");
  } catch (err) {
    document.querySelector("main").innerHTML = `<p class="empty">Failed to load: ${esc(err.message)}</p>`;
  }
})();
