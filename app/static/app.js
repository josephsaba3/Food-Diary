"use strict";
const $ = (selector) => document.querySelector(selector);
const csrf = $('meta[name="csrf-token"]').content;
let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}
async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, ...options.headers } });
  } catch {
    throw new Error("Couldn’t reach your diary. Check your connection and try again. Your changes are still here.");
  }
  if (!response.ok) {
    let data;
    try { data = await response.json(); } catch { data = {}; }
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(" ") : data.detail;
    throw new Error(detail || "Couldn’t save this change. Please try again.");
  }
  return response.status === 204 ? null : response.json();
}
function showError(selector, error) { const el = $(selector); el.textContent = error.message; el.hidden = false; }

const diary = $(".diary-layout");
if (diary) {
  const day = diary.dataset.day;
  const dialog = $("#meal-dialog");
  const form = $("#meal-form");
  const categories = {breakfast:"Breakfast",mid_morning_snack:"Mid morning snack",lunch:"Lunch",mid_afternoon_snack:"Mid afternoon snack",dinner:"Dinner",evening_snack:"Evening snack"};
  let data = null, category = null, original = "", notesOriginal = $("#day-notes").value, loading = false, saving = false;
  let notesVersion = Number($("#notes-form").dataset.version);
  // An empty time picker opens on the current time, so seed a typical time for the meal instead;
  // morning meals then open on AM and the rest on PM.
  const typicalTimes = {breakfast:"08:00",mid_morning_snack:"10:30",lunch:"12:30",mid_afternoon_snack:"15:30",dinner:"18:30",evening_snack:"20:30"};
  function snapshot() { return JSON.stringify(Object.fromEntries(new FormData(form))); }
  function seedTime() { if (!$("#meal-time").value && category) $("#meal-time").value = typicalTimes[category]; }
  ["pointerdown", "focus"].forEach(type => $("#meal-time").addEventListener(type, seedTime));
  function dirty() { return dialog.open && snapshot() !== original; }
  const initialLoad = api(`/api/days/${day}`).then(result => { data = result; }).catch(error => { toast(error.message); });
  function statusField() {
    const reported = form.elements.symptom_status.value === "reported";
    $("#symptoms-field").hidden = !reported;
    $("#meal-symptoms").required = reported;
  }
  form.querySelectorAll('[name="symptom_status"]').forEach(input => input.addEventListener("change", () => {
    if (form.elements.symptom_status.value !== "reported" && $("#meal-symptoms").value && !confirm("Clear the symptoms you’ve entered?")) {
      form.elements.symptom_status.value = "reported";
    } else if (form.elements.symptom_status.value !== "reported") { $("#meal-symptoms").value = ""; }
    statusField();
  }));
  async function openMeal(key, importFood = false) {
    if (loading) return;
    loading = true;
    try {
      // Read afresh so Claude edits appear before the form is opened.
      const latest = await api(`/api/days/${day}`);
      if (data) { data.meals = latest.meals; data.suggestions = latest.suggestions; }
      else data = latest;
      category = key;
      form.reset();
      const meal = data.meals[key];
      $("#editor-title").textContent = categories[key];
      $("#meal-food").value = meal?.food || "";
      $("#meal-time").value = meal?.time || "";
      $("#meal-symptoms").value = meal?.symptoms || "";
      form.elements.symptom_status.value = meal?.symptom_status || "unrecorded";
      $("#meal-error").hidden = true;
      $("#import-feedback").hidden = true;
      $("#delete-meal").hidden = !meal;
      $("#cancel-meal").hidden = !!meal;
      const suggestion = data.suggestions[key];
      $("#editor-import").hidden = !suggestion;
      if (suggestion) $("#editor-import").textContent = `${suggestion.label}: ${suggestion.food}`;
      statusField();
      original = snapshot();
      if (importFood && suggestion) copyFood();
      dialog.showModal();
      document.body.classList.add("editor-open");
      // Keep the keyboard closed for imports so time + food can be reviewed first.
      if (!importFood && !meal) $("#meal-food").focus();
    } catch (error) { toast(error.message); }
    finally { loading = false; }
  }
  function copyFood() {
    const suggestion = data.suggestions[category];
    if (!suggestion) return;
    if ($("#meal-food").value && $("#meal-food").value !== suggestion.food && !confirm("Replace the food description with yesterday’s meal?")) return;
    $("#meal-food").value = suggestion.food;
    $("#import-feedback").hidden = false;
  }
  function closeEditor() {
    if (saving || (dirty() && !confirm("Discard your unsaved meal changes?"))) return;
    dialog.close();
  }
  dialog.addEventListener("close", () => document.body.classList.remove("editor-open"));
  dialog.addEventListener("cancel", event => { event.preventDefault(); closeEditor(); });
  document.querySelectorAll(".close-editor").forEach(button => button.addEventListener("click", closeEditor));
  $("#editor-import").addEventListener("click", copyFood);
  document.querySelectorAll("[data-edit]").forEach(button => button.addEventListener("click", () => openMeal(button.dataset.edit)));
  document.querySelectorAll("[data-import]").forEach(button => button.addEventListener("click", () => openMeal(button.dataset.import, true)));
  function renderMeal(key) {
    const row = $(`#meal-${key}`), meal = data.meals[key];
    row.classList.toggle("has-entry", !!meal);
    row.querySelector(".meal-open").setAttribute("aria-label", `${meal ? "Edit" : "Add"} ${categories[key].toLowerCase()}`);
    row.querySelector(".meal-food").textContent = meal?.food || "Add food & drink";
    row.querySelector(".meal-food").classList.toggle("muted", !meal);
    row.querySelector(".meal-time").textContent = meal?.time || "";
    row.querySelector(".meal-marker path").setAttribute("d", meal ? "m5 12 4 4L19 6" : "M12 5v14M5 12h14");
    let symptom = row.querySelector(".symptom-preview");
    if (!symptom && meal?.symptoms) { symptom = document.createElement("span"); symptom.className = "symptom-preview"; row.querySelector(".meal-main").append(symptom); }
    if (symptom) { symptom.textContent = meal?.symptoms || ""; symptom.hidden = !meal?.symptoms; }
    row.querySelector(".import-suggestion")?.remove();
    if (!meal && data.suggestions[key]) {
      const button = document.createElement("button"); button.className = "import-suggestion"; button.type = "button";
      button.textContent = `${data.suggestions[key].label}: ${data.suggestions[key].food}`;
      button.addEventListener("click", () => openMeal(key, true)); row.append(button);
    }
    const count = Object.keys(data.meals).length;
    $("#entry-count").textContent = `${count} ${count === 1 ? "entry" : "entries"}`;
  }
  function busy(value) {
    saving = value;
    form.querySelectorAll("button, input, textarea").forEach(control => control.disabled = value);
    $(".save-meal").textContent = value ? "Saving…" : "Save meal";
  }
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (saving) return;
    const fields = Object.fromEntries(new FormData(form));
    const payload = {...fields, time: fields.time || null, expected_version: data.meals[category]?.version || 0};
    $("#meal-error").hidden = true; busy(true);
    try {
      data.meals[category] = await api(`/api/days/${day}/meals/${category}`, {method:"PUT",body:JSON.stringify(payload)});
      renderMeal(category); original = snapshot(); dialog.close(); toast(`${categories[category]} saved`);
    } catch (error) { showError("#meal-error", error); $("#meal-error").scrollIntoView({block:"nearest"}); }
    finally { busy(false); }
  });
  $("#delete-meal").addEventListener("click", async () => {
    if (!confirm(`Delete this ${categories[category].toLowerCase()} entry?`)) return;
    busy(true);
    try { await api(`/api/days/${day}/meals/${category}?version=${data.meals[category].version}`, {method:"DELETE"}); delete data.meals[category]; renderMeal(category); original = snapshot(); dialog.close(); toast("Meal deleted"); }
    catch (error) { showError("#meal-error", error); }
    finally { busy(false); }
  });
  $("#day-notes").addEventListener("input", () => { $("#notes-status").textContent = $("#day-notes").value === notesOriginal ? "" : "Unsaved changes"; });
  $("#notes-form").addEventListener("submit", async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true; $("#notes-error").hidden = true;
    $("#day-notes").disabled = true; button.textContent = "Saving…";
    try {
      await initialLoad;
      if (!data) throw new Error("The diary hasn’t loaded. Reload the page before saving notes.");
      const text = $("#day-notes").value;
      data.note = await api(`/api/days/${day}/notes`, {method:"PUT",body:JSON.stringify({text,expected_version:notesVersion})});
      notesVersion = data.note.version;
      notesOriginal = text; $("#notes-status").textContent = "Saved"; toast("Notes saved");
    } catch (error) { showError("#notes-error", error); }
    finally { button.disabled = false; $("#day-notes").disabled = false; button.textContent = "Save notes"; }
  });
  function navigationAllowed() { return !dirty() && $("#day-notes").value === notesOriginal || confirm("Leave this day and discard unsaved changes?"); }
  $("#day-picker").addEventListener("change", event => {
    if (event.target.value && navigationAllowed()) { notesOriginal = $("#day-notes").value; original = snapshot(); location.href = `/?date=${event.target.value}`; }
    else event.target.value = day;
  });
  window.addEventListener("beforeunload", event => { if (dirty() || $("#day-notes").value !== notesOriginal) { event.preventDefault(); event.returnValue = ""; } });
}
$("#copy-url")?.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#mcp-url").value); toast("Server URL copied"); }
  catch { $("#mcp-url").select(); toast("Select and copy the server URL"); }
});
$("#disconnect")?.addEventListener("click", async event => {
  if (!confirm("Disconnect all Claude and MCP connections? You can connect again later.")) return;
  event.target.disabled = true;
  try { await api("/api/disconnect",{method:"POST"}); $("#connection-status").textContent = "All connections disconnected."; }
  catch (error) { $("#connection-status").textContent = error.message; }
  finally { event.target.disabled = false; }
});
$("#import-form")?.addEventListener("submit", async event => {
  event.preventDefault(); const file = $("#import-file").files[0]; if (!file) return;
  const button = event.submitter; button.disabled = true; $("#import-status").textContent = "Importing…";
  try {
    if (file.size > 2000000) throw new Error("Choose a file smaller than 2 MB.");
    const result = await api("/api/import",{method:"POST",body:await file.text()});
    $("#import-status").textContent = `${result.added} meals imported; ${result.skipped} existing meals skipped; ${result.notes_added} daily notes imported.`;
  } catch (error) { $("#import-status").textContent = error.message; }
  finally { button.disabled = false; }
});

async function garminPost(url, body, button, done) {
  const status = $("#garmin-status");
  if (button) button.disabled = true;
  status.textContent = "Talking to Garmin…";
  try { const result = await api(url, {method:"POST", body:JSON.stringify(body)}); done(result); }
  catch (error) { status.textContent = error.message; }
  finally { if (button) button.disabled = false; }
}
const formBody = form => Object.fromEntries(new FormData(form));
document.querySelectorAll(".garmin-key-form").forEach(form => form.addEventListener("submit", event => {
  event.preventDefault();
  garminPost("/api/garmin/key", formBody(form), event.submitter, () => location.reload());
}));
// Long Garmin downloads run on the server; poll until they finish.
async function watchHistory(statusEl, onDone) {
  for (;;) {
    let job;
    try { job = await api("/api/garmin/history"); } catch (error) { statusEl.textContent = error.message; return; }
    if (job.state === "running") {
      const progress = `${job.done_days} of ${job.total_days} days`;
      statusEl.textContent = job.waiting_until
        ? `Garmin asked to slow down, so the download is waiting. It carries on at ${new Date(job.waiting_until).toLocaleTimeString("en-AU", {hour:"numeric", minute:"2-digit"})} · ${progress}`
        : `Downloading from Garmin a week at a time… ${progress}`;
      await new Promise(resolve => setTimeout(resolve, 1500));
      continue;
    }
    if (job.state !== "idle") onDone(job);
    return;
  }
}
function historySummary(job) {
  const days = `${job.days_synced} ${job.days_synced === 1 ? "day" : "days"} with data`;
  const workouts = `${job.activities_synced} ${job.activities_synced === 1 ? "workout" : "workouts"}`;
  const skipped = job.skipped_days ? `, ${job.skipped_days} already up to date` : "";
  return `${days}, ${workouts}${skipped}.${job.error ? " " + job.error : ""}`;
}
async function startHistory(body, button, statusEl, onDone) {
  if (button) button.disabled = true;
  statusEl.textContent = "Starting…";
  try { await api("/api/garmin/history", {method:"POST", body:JSON.stringify(body)}); }
  catch (error) {
    // Another download already running: follow it instead of failing.
    if (!/already running/.test(error.message)) { statusEl.textContent = error.message; if (button) button.disabled = false; return; }
  }
  await watchHistory(statusEl, job => { if (button) button.disabled = false; onDone(job); });
  if (button) button.disabled = false;
}
$("#garmin-history-form")?.addEventListener("submit", event => {
  event.preventDefault();
  startHistory({days:Number($("#garmin-days").value)}, event.submitter, $("#garmin-status"), job => { $("#garmin-status").textContent = historySummary(job); });
});
if ($("#garmin-history-form") && $("#garmin-history-form").dataset.running === "true") {
  watchHistory($("#garmin-status"), job => { $("#garmin-status").textContent = historySummary(job); });
}
$("#garmin-disconnect")?.addEventListener("click", event => {
  if (!confirm("Unlink Garmin? Health data already synced stays in your diary.")) return;
  garminPost("/api/garmin/disconnect", {}, event.target, () => location.reload());
});
$("#garmin-sync")?.addEventListener("click", async event => {
  const button = event.currentTarget, day = button.dataset.day;
  button.disabled = true; button.lastChild.textContent = "Syncing…";
  try {
    await api("/api/garmin/sync", {method:"POST", body:JSON.stringify({start:day, end:day})});
    const unsaved = $("#meal-dialog")?.open || ($("#notes-status")?.textContent === "Unsaved changes");
    if (unsaved) toast("Garmin synced. Save your changes, then reload to see it.");
    else location.reload();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.lastChild.textContent = "Sync"; }
});

// The page's CSP forbids inline style attributes, so chart geometry arrives as data-pos="prop:value;…"
// and is applied through the CSSOM, which the policy allows.
function applyPos(root) {
  root.querySelectorAll("[data-pos]").forEach(node => {
    node.dataset.pos.split(";").forEach(pair => {
      const at = pair.indexOf(":");
      if (at > 0) node.style.setProperty(pair.slice(0, at).trim(), pair.slice(at + 1).trim());
    });
  });
}
applyPos(document);

// Through the day: read a 15-minute slot under the pointer, and swap in another day's charts.
function bindTimeline(section) {
  const charts = section?.querySelector(".tcharts"), readout = section?.querySelector(".t-readout");
  if (!charts || !readout) return;
  const slots = new Map(JSON.parse(charts.dataset.slots || "[]").map(slot => [slot.time, slot]));
  const resting = readout.textContent, cursors = section.querySelectorAll(".tcursor");
  const pad = n => String(n).padStart(2, "0");
  function read(event) {
    const box = event.currentTarget.getBoundingClientRect();
    const minute = Math.min(1439, Math.max(0, Math.floor((event.clientX - box.left) / box.width * 1440)));
    const start = minute - minute % 15, time = `${pad(Math.floor(start / 60))}:${pad(start % 60)}`;
    const end = (start + 15) % 1440, until = `${pad(Math.floor(end / 60))}:${pad(end % 60)}`;
    const slot = slots.get(time);
    const parts = [`${time}–${until}`];
    if (!slot) parts.push("no readings");
    else {
      if (slot.hr != null) parts.push(`${slot.hr} bpm avg (${slot.hr_min}–${slot.hr_max})`);
      if (slot.body_battery != null) parts.push(`Body Battery ${slot.body_battery}`);
      if (slot.stress != null) parts.push(`stress ${slot.stress}${slot.stress_max > slot.stress ? ` (peak ${slot.stress_max})` : ""}`);
    }
    readout.textContent = parts.join(" · ");
    readout.classList.add("on");
    cursors.forEach(cursor => { cursor.hidden = false; cursor.style.left = `${(start + 7.5) / 1440 * 100}%`; });
  }
  function reset() {
    readout.textContent = resting; readout.classList.remove("on");
    cursors.forEach(cursor => { cursor.hidden = true; });
  }
  section.querySelectorAll(".tplot").forEach(plot => {
    plot.addEventListener("pointermove", read);
    plot.addEventListener("pointerdown", read);
    plot.addEventListener("pointerleave", reset);
  });
}
bindTimeline($("#timeline"));
let timelineRequest = 0;
async function showTimeline(iso) {
  const current = $("#timeline");
  if (!current || current.dataset.day === iso) return;
  const ticket = ++timelineRequest;
  current.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/health/timeline?day=${encodeURIComponent(iso)}`, {credentials: "same-origin"});
    if (!response.ok) throw new Error(String(response.status));
    const html = await response.text();
    if (ticket !== timelineRequest) return;
    const holder = document.createElement("div");
    holder.innerHTML = html;
    const next = holder.querySelector("#timeline");
    if (!next) return;
    applyPos(next);
    current.replaceWith(next);
    bindTimeline(next);
  } catch (error) {
    if (ticket === timelineRequest) current.removeAttribute("aria-busy");
  }
}
const health = $(".health-layout");
if (health && $("#health-data")) {
  const days = JSON.parse($("#health-data").textContent);
  let selected = Number(health.dataset.selected), sheetIndex = selected;
  const fmt = n => n == null ? "–" : Math.round(n).toLocaleString("en-AU");
  const hm = minutes => `${Math.floor(minutes / 60)}h ${String(Math.round(minutes % 60)).padStart(2, "0")}m`;
  const label = value => value ? String(value).replace(/_/g, " ").toLowerCase().replace(/^./, c => c.toUpperCase()) : "";
  const shortDate = iso => { const d = new Date(iso + "T00:00:00"); return d.toLocaleDateString("en-AU", {day:"numeric", month:"short"}); };
  function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; }
  function setValue(node, value, unit) {
    node.replaceChildren();
    if (value == null) { node.append(el("span", "muted no-reading", "No readings")); return; }
    node.append(String(value));
    if (unit) node.append(el("small", null, " " + unit));
  }
  function show(i) {
    const day = days[i];
    if (!day) return;
    document.querySelectorAll(".col.sel").forEach(col => col.classList.remove("sel"));
    document.querySelectorAll(`.col[data-i="${i}"]`).forEach(col => col.classList.add("sel"));
    $("#sel-title").textContent = day.long;
    $("#sel-status").textContent = day.worn ? "Watch worn" + (day.workout ? " · " + day.workout : "") : "No watch readings this day";
    $("#sel-view").href = `/?date=${day.iso}`;
    const worn = day.worn;
    setValue($('[data-value="steps"]'), worn ? fmt(day.steps) : null, "steps");
    setValue($('[data-value="rhr"]'), worn ? day.rhr : null, "bpm");
    setValue($('[data-value="stress"]'), worn ? (day.stress ?? "–") : null, "avg");
    setValue($('[data-value="bb"]'), worn ? (day.bb_high != null ? `${day.bb_low}–${day.bb_high}` : "–") : null, "low–high");
  }
  function select(i) { selected = i; show(i); if (days[i]) showTimeline(days[i].iso); }
  document.querySelectorAll(".col").forEach(col => {
    col.addEventListener("click", () => select(Number(col.dataset.i)));
    col.addEventListener("mouseenter", () => show(Number(col.dataset.i)));
  });
  document.querySelectorAll(".plot").forEach(plot => plot.addEventListener("mouseleave", () => show(selected)));

  // Day sheet ------------------------------------------------------------------
  const sheet = $("#day-sheet");
  function tile(title, value, unit, sub, muted = false) {
    const box = el("div", "health-tile"), dd = el("dd", muted ? "muted-value" : null, String(value));
    if (unit) dd.append(el("small", null, " " + unit));
    box.append(el("dt", null, title), dd);
    if (sub) box.append(el("span", null, sub));
    return box;
  }
  const join = parts => parts.filter(Boolean).join(" · ");
  function tilesFor(day) {
    const s = day.summary || {}, out = [];
    const st = s.steps, hr = s.heart_rate, stress = s.stress, bb = s.body_battery, cal = s.calories, im = s.intensity_minutes;
    if (st?.total != null) out.push(tile("Steps", fmt(st.total), null, join([st.goal ? `Goal ${fmt(st.goal)}` : "", st.distance_km ? `${st.distance_km} km` : ""])));
    if (hr?.resting != null) out.push(tile("Resting heart rate", hr.resting, "bpm", join([hr.min != null ? `Range ${hr.min}–${hr.max}` : "", hr.resting_7_day_avg ? `7-day ${hr.resting_7_day_avg}` : ""])));
    if (stress?.average != null) out.push(tile("Stress", stress.average, "avg", join([stress.max != null ? `Peak ${stress.max}` : "", stress.high_minutes ? `${hm(stress.high_minutes)} high` : ""])));
    if (bb?.highest != null) out.push(tile("Body Battery", bb.highest, "high", join([bb.lowest != null ? `Low ${bb.lowest}` : "", bb.charged != null ? `+${bb.charged} / −${bb.drained ?? 0}` : ""])));
    if (s.sleep?.total_minutes != null) out.push(tile("Sleep", hm(s.sleep.total_minutes), null, join([s.sleep.score != null ? `Score ${s.sleep.score}` : "", label(s.sleep.quality)])));
    if (s.hrv?.last_night_avg != null) out.push(tile("Overnight HRV", s.hrv.last_night_avg, "ms", label(s.hrv.status)));
    if (cal?.active != null) out.push(tile("Active calories", fmt(cal.active), null, cal.total != null ? `${fmt(cal.total)} total` : ""));
    if (im && (im.moderate != null || im.vigorous != null)) out.push(tile("Intensity minutes", (im.moderate || 0) + 2 * (im.vigorous || 0), null, `${im.moderate || 0} moderate · ${im.vigorous || 0} vigorous`));
    if (s.respiration?.waking_avg != null) out.push(tile("Breathing", Math.round(s.respiration.waking_avg), "/min", "Awake average"));
    const acts = day.activities || [];
    if (!acts.length) out.push(tile("Workouts", "None", null, "No activity recorded", true));
    else if (acts.length === 1) {
      const a = acts[0];
      out.push(tile("Workout", a.name || label(a.type), null, join([a.duration_minutes ? `${Math.round(a.duration_minutes)} min` : "", a.distance_km ? `${a.distance_km} km` : "", a.avg_hr ? `${a.avg_hr} bpm avg` : ""])));
    } else out.push(tile("Workouts", acts.length, null, acts.map(a => a.name || label(a.type)).join(", ")));
    return out;
  }
  function fillSheet(i) {
    const day = days[i];
    sheetIndex = i;
    $("#sheet-title").textContent = day.long;
    const synced = day.synced_at ? `synced ${shortDate(day.synced_at.slice(0, 10))}` : "not synced yet";
    $("#sheet-status").textContent = `${day.worn ? "Watch worn" : "No watch readings"} · ${synced}`;
    $("#sheet-empty").hidden = day.worn;
    $("#sheet-tiles").replaceChildren(...(day.worn ? tilesFor(day) : []));
    $("#sheet-meals").href = $("#sheet-open").href = `/?date=${day.iso}`;
    $("#sheet-meals-sub").textContent = `What you logged in the diary on ${shortDate(day.iso)}`;
    const prev = days[i - 1], next = days[i + 1];
    $("#sheet-prev").disabled = !prev; $("#sheet-next").disabled = !next;
    $("#sheet-prev").setAttribute("aria-label", prev ? `Previous day: ${prev.long}` : "Previous day");
    $("#sheet-next").setAttribute("aria-label", next ? `Next day: ${next.long}` : "Next day");
  }
  function openSheet(i) { fillSheet(i); select(i); if (!sheet.open) { sheet.showModal(); document.body.classList.add("editor-open"); } }
  sheet.addEventListener("close", () => document.body.classList.remove("editor-open"));
  sheet.addEventListener("click", event => { if (event.target === sheet) sheet.close(); });
  $("[data-close-sheet]").addEventListener("click", () => sheet.close());
  $("#sheet-prev").addEventListener("click", () => openSheet(sheetIndex - 1));
  $("#sheet-next").addEventListener("click", () => openSheet(sheetIndex + 1));
  $("#sel-view").addEventListener("click", event => { event.preventDefault(); openSheet(selected); });
  document.querySelectorAll("[data-open-day]").forEach(link => link.addEventListener("click", event => { event.preventDefault(); openSheet(Number(link.dataset.openDay)); }));
  $("#show-all-days")?.addEventListener("click", event => { $(".log-section").classList.add("show-more"); event.target.hidden = true; });
}
const periodForm = $("#period-form");
if (periodForm) {
  const go = params => { location.href = "/health?" + new URLSearchParams(params); };
  periodForm.querySelectorAll('[name="period"]').forEach(radio => radio.addEventListener("change", () => {
    if (radio.value === "custom") { periodForm.classList.add("custom"); periodForm.elements.start.focus(); }
    else go({period: radio.value});
  }));
  if (periodForm.elements.period.value === "custom") periodForm.classList.add("custom");
  ["start", "end"].forEach(name => periodForm.elements[name].addEventListener("change", () => {
    const start = periodForm.elements.start.value, end = periodForm.elements.end.value;
    if (start && end) go({start, end});
  }));
}
document.querySelectorAll("[data-health-sync]").forEach(button => button.addEventListener("click", () => {
  const layout = $(".health-layout"), status = $("#health-sync-status"), alert = $("#health-alert");
  const buttons = document.querySelectorAll("[data-health-sync]");
  buttons.forEach(b => b.disabled = true); alert.hidden = true;
  startHistory({start: layout.dataset.start, end: layout.dataset.end}, null, status, job => {
    if (job.error) {
      alert.textContent = /limiting/.test(job.error) ? `${job.error} Your saved days still show below.` : job.error;
      alert.hidden = false; status.textContent = job.days_synced ? historySummary({...job, error: null}) : "";
      if (!/limiting/.test(job.error)) buttons.forEach(b => b.disabled = false);
      return;
    }
    status.textContent = "Synced. Updating…";
    location.reload();
  }).finally(() => { if (alert.hidden) buttons.forEach(b => b.disabled = false); });
}));
if ($(".health-layout") && $("[data-health-sync]")) {
  // Pick up a download started elsewhere (Settings, or another tab).
  api("/api/garmin/history").then(job => { if (job.state === "running") watchHistory($("#health-sync-status"), () => location.reload()); }).catch(() => {});
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
