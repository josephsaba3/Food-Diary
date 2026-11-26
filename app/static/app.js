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

// Flash messages survive the reload that follows a save.
try { const flash = sessionStorage.getItem("flash"); if (flash) { sessionStorage.removeItem("flash"); setTimeout(() => toast(flash), 50); } } catch {}
function reloadWith(message, url) {
  try { sessionStorage.setItem("flash", message); } catch {}
  if (url) location.href = url; else location.reload();
}
// Sheets: <dialog> with a dirty check before closing.
function sheetControls(dialog, isDirty) {
  const close = () => { if (!isDirty() || confirm("Discard your unsaved changes?")) dialog.close(); };
  dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
  dialog.addEventListener("click", event => { if (event.target === dialog) close(); });
  dialog.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", close));
  dialog.addEventListener("close", () => document.body.classList.remove("sheet-open"));
  return () => { dialog.showModal(); document.body.classList.add("sheet-open"); };
}

const today = $(".page.today");
if (today) {
  const data = JSON.parse($("#today-data").textContent);
  const pageDay = data.day;
  const slots = data.slots;
  const typical = {breakfast:"08:00",mid_morning_snack:"10:30",lunch:"12:30",mid_afternoon_snack:"15:30",dinner:"18:30",evening_snack:"20:30"};
  const SYMPTOMS = [...document.querySelectorAll("[data-symptom]")].map(b => b.dataset.symptom);
  const SEVERITY = ["none", "very mild", "mild", "moderate", "strong", "severe"];
  const HINTS = {
    unrecorded: "Saved as not recorded. You can add how you felt later.",
    none: "Saved as no symptoms, so it’s clear this wasn’t left blank.",
    reported: "Pick what you noticed. Tap again to remove.",
  };
  const sheet = $("#log-sheet"), form = $("#log-form");
  const state = {day: pageDay, meals: data.meals, category: null, chips: [], status: "unrecorded",
                 tags: new Set(), sev: null, stool: null, saving: false, original: ""};

  // Symptoms are free text. The shortcuts write "Bloating, cramps · severity 2/5 (mild) · Bristol type 5 · notes"
  // and read the same pattern back; anything else is kept as notes.
  function composeSymptoms() {
    const parts = [];
    const tags = SYMPTOMS.filter(s => state.tags.has(s));
    if (tags.length) parts.push(tags.map((t, i) => i ? t.toLowerCase() : t).join(", "));
    if (state.sev !== null) parts.push(`severity ${state.sev}/5 (${SEVERITY[state.sev]})`);
    if (state.stool !== null) parts.push(`Bristol type ${state.stool}`);
    const notes = $("#symptom-notes").value.trim();
    if (notes) parts.push(notes);
    return parts.join(" · ");
  }
  function parseSymptoms(text) {
    state.tags = new Set(); state.sev = null; state.stool = null;
    const rest = [];
    (text || "").split(" · ").forEach((part, index) => {
      const sev = part.match(/^severity ([0-5])\/5\b/i), stool = part.match(/^Bristol type ([1-7])$/i);
      const words = part.split(/,\s*/).map(w => SYMPTOMS.find(s => s.toLowerCase() === w.trim().toLowerCase()));
      if (sev) state.sev = Number(sev[1]);
      else if (stool) state.stool = Number(stool[1]);
      else if (index === 0 && part && words.every(Boolean)) words.forEach(w => state.tags.add(w));
      else rest.push(part);
    });
    $("#symptom-notes").value = rest.join(" · ");
  }
  function snapshot() { return JSON.stringify([state.day, state.category, state.chips, $("#log-time").value, state.status, composeSymptoms()]); }
  const isDirty = () => sheet.open && !state.saving && snapshot() !== state.original;
  const openSheet = sheetControls(sheet, isDirty);

  function renderChips() {
    const list = $("#food-chips");
    list.replaceChildren(...state.chips.map((item, i) => {
      const li = document.createElement("li"); li.className = "chip removable"; li.textContent = item;
      const x = document.createElement("button"); x.type = "button"; x.setAttribute("aria-label", `Remove ${item}`); x.textContent = "×";
      x.addEventListener("click", () => { state.chips.splice(i, 1); renderChips(); $("#food-input").focus(); });
      li.append(x); return li;
    }));
    $("#chip-input").classList.toggle("has-chips", state.chips.length > 0);
  }
  function addFood(text) {
    text.split(/\s*(?:\n|;|,|\s\+\s)\s*/).map(t => t.trim()).filter(Boolean).forEach(item => {
      if (!state.chips.some(c => c.toLowerCase() === item.toLowerCase())) state.chips.push(item);
    });
    renderChips();
  }
  function renderFeeling() {
    form.querySelectorAll("[data-status]").forEach(b => b.setAttribute("aria-checked", String(b.dataset.status === state.status)));
    $("#feel-hint").textContent = HINTS[state.status];
    $("#symptom-fields").hidden = state.status !== "reported";
    form.querySelectorAll("[data-symptom]").forEach(b => b.setAttribute("aria-pressed", String(state.tags.has(b.dataset.symptom))));
    form.querySelectorAll("[data-sev]").forEach(b => b.setAttribute("aria-checked", String(Number(b.dataset.sev) === state.sev)));
    form.querySelectorAll("[data-stool]").forEach(b => b.setAttribute("aria-checked", String(Number(b.dataset.stool) === state.stool)));
    $("#sev-value").textContent = state.sev === null ? "" : `${state.sev} · ${SEVERITY[state.sev][0].toUpperCase()}${SEVERITY[state.sev].slice(1)}`;
    $("#symptom-preview").textContent = composeSymptoms() || "nothing yet";
  }
  function renderSlot() {
    const meal = state.meals[state.category];
    form.querySelectorAll("[data-slot]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.slot === state.category)));
    $("#delete-meal").hidden = !meal;
    $("#log-date").disabled = !!meal;
    $("#save-meal").textContent = meal ? "Save changes" : `Save to ${slots[state.category].toLowerCase()}`;
    const s = data.suggestions[state.category] || {often: [], repeat: null};
    const sameDay = state.day === pageDay;
    $("#often").hidden = !sameDay || !s.often.length;
    $("#often-list").replaceChildren(...(sameDay ? s.often : []).map(o => {
      const b = document.createElement("button"); b.type = "button"; b.className = "pill add"; b.textContent = `+ ${o.label}`;
      b.addEventListener("click", () => addFood(o.food)); return b;
    }));
    $("#repeat-btn").hidden = !sameDay || !s.repeat || !!meal;
    if (s.repeat) { $("#repeat-label").textContent = s.repeat.label; $("#repeat-food").textContent = s.repeat.food; }
  }
  function updateNowTag() { $("#now-tag").hidden = !(state.day === data.today && $("#log-time").value === data.now); }
  function load(category) {
    state.category = category;
    const meal = state.meals[category];
    state.chips = [];
    if (meal) addFood(meal.food); else renderChips();
    $("#log-time").value = meal ? (meal.time || "") : (state.day === data.today ? data.now : typical[category]);
    state.status = meal?.symptom_status || "unrecorded";
    parseSymptoms(meal?.symptom_status === "reported" ? meal.symptoms : "");
    $("#food-input").value = ""; $("#log-error").hidden = true;
    $("#sheet-title").textContent = meal ? `Edit ${slots[category].toLowerCase()}` : (state.day === data.today ? "Add to today" : `Add to ${new Date(state.day + "T00:00").toLocaleDateString("en-AU", {weekday:"short", day:"numeric", month:"short"})}`);
    renderSlot(); renderFeeling(); updateNowTag();
  }
  function firstEmptySlot() {
    const keys = Object.keys(slots), now = data.now;
    const free = keys.filter(k => !state.meals[k]);
    if (state.day === data.today) return free.find(k => typical[k] >= now) || free[free.length - 1] || keys[0];
    return free[0] || keys[0];
  }
  async function refresh(day) {
    const latest = await api(`/api/days/${day}`);
    state.meals = latest.meals;
  }
  async function start({category, food, focus} = {}) {
    try { state.day = pageDay; $("#log-date").value = pageDay; await refresh(pageDay); }
    catch (error) { toast(error.message); return; }
    load(category || firstEmptySlot());
    if (food && !state.meals[state.category]) { addFood(food); }
    state.original = snapshot();
    if (food) state.original = "";  // a copied meal counts as a change
    openSheet();
    if (focus === "feel") $("#feeling").scrollIntoView({block: "start"});
    else if (!state.meals[state.category] && !food) $("#food-input").focus();
  }

  form.querySelectorAll("[data-slot]").forEach(b => b.addEventListener("click", () => {
    if (b.dataset.slot === state.category) return;
    const keep = state.meals[state.category] ? [] : [...state.chips];
    const time = $("#log-time").value, status = state.status, symptoms = composeSymptoms();
    load(b.dataset.slot);
    if (!state.meals[state.category]) {  // carry what was typed over to the newly chosen slot
      keep.forEach(c => addFood(c));
      if (state.day !== data.today || time !== data.now) $("#log-time").value = time || typical[state.category];
      state.status = status; parseSymptoms(symptoms); renderFeeling(); updateNowTag();
    }
  }));
  $("#log-date").addEventListener("change", async event => {
    const value = event.target.value;
    if (!value) { event.target.value = state.day; return; }
    try { await refresh(value); state.day = value; }
    catch (error) { toast(error.message); event.target.value = state.day; return; }
    const chips = [...state.chips];
    load(state.category);
    if (!state.meals[state.category]) chips.forEach(c => addFood(c));
  });
  $("#log-time").addEventListener("input", updateNowTag);
  $("#food-input").addEventListener("keydown", event => {
    const input = event.target;
    if ((event.key === "Enter" || event.key === ",") && input.value.trim()) { event.preventDefault(); addFood(input.value); input.value = ""; }
    else if (event.key === "Enter") event.preventDefault();
    else if (event.key === "Backspace" && !input.value && state.chips.length) { state.chips.pop(); renderChips(); }
  });
  $("#food-input").addEventListener("input", event => { if (event.target.value.includes(",")) { addFood(event.target.value); event.target.value = ""; } });
  $("#chip-input").addEventListener("click", event => { if (event.target === event.currentTarget) $("#food-input").focus(); });
  $("#repeat-btn").addEventListener("click", () => addFood(data.suggestions[state.category].repeat.food));
  form.querySelectorAll("[data-status]").forEach(b => b.addEventListener("click", () => {
    if (state.status === "reported" && b.dataset.status !== "reported" && composeSymptoms() && !confirm("Clear the symptoms you’ve entered?")) return;
    state.status = b.dataset.status;
    if (state.status !== "reported") { state.tags.clear(); state.sev = null; state.stool = null; $("#symptom-notes").value = ""; }
    renderFeeling();
  }));
  form.querySelectorAll("[data-symptom]").forEach(b => b.addEventListener("click", () => {
    const s = b.dataset.symptom; state.tags.has(s) ? state.tags.delete(s) : state.tags.add(s); renderFeeling();
  }));
  form.querySelectorAll("[data-sev]").forEach(b => b.addEventListener("click", () => { const n = Number(b.dataset.sev); state.sev = state.sev === n ? null : n; renderFeeling(); }));
  form.querySelectorAll("[data-stool]").forEach(b => b.addEventListener("click", () => { const n = Number(b.dataset.stool); state.stool = state.stool === n ? null : n; renderFeeling(); }));
  $("#symptom-notes").addEventListener("input", () => { $("#symptom-preview").textContent = composeSymptoms() || "nothing yet"; });

  function busy(value) {
    state.saving = value;
    form.querySelectorAll("button, input, textarea").forEach(c => { if (c.id !== "log-date" || !state.meals[state.category]) c.disabled = value; });
    if (value) $("#save-meal").textContent = "Saving…"; else renderSlot();
  }
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (state.saving) return;
    if ($("#food-input").value.trim()) { addFood($("#food-input").value); $("#food-input").value = ""; }
    const symptoms = state.status === "reported" ? composeSymptoms() : "";
    const fail = message => { $("#log-error").textContent = message; $("#log-error").hidden = false; $("#log-error").scrollIntoView({block: "nearest"}); };
    if (!state.chips.length) return fail("Add at least one food or drink.");
    if (state.status === "reported" && !symptoms) return fail("Pick a symptom or describe it, or choose Not now.");
    const meal = state.meals[state.category];
    const payload = {food: state.chips.join(", "), time: $("#log-time").value || null, symptoms, symptom_status: state.status,
                     expected_version: meal?.version || 0};
    $("#log-error").hidden = true; busy(true);
    try {
      await api(`/api/days/${state.day}/meals/${state.category}`, {method: "PUT", body: JSON.stringify(payload)});
      state.original = snapshot(); sheet.close();
      const label = slots[state.category];
      if (state.day === pageDay) reloadWith(`${label} saved`);
      else reloadWith(`${label} saved to ${new Date(state.day + "T00:00").toLocaleDateString("en-AU", {weekday: "long", day: "numeric", month: "long"})}`, `/?date=${state.day}`);
    } catch (error) { fail(error.message); }
    finally { busy(false); }
  });
  $("#delete-meal").addEventListener("click", async () => {
    const meal = state.meals[state.category];
    if (!meal || !confirm(`Delete this ${slots[state.category].toLowerCase()}?`)) return;
    busy(true);
    try { await api(`/api/days/${state.day}/meals/${state.category}?version=${meal.version}`, {method: "DELETE"}); state.original = snapshot(); sheet.close(); reloadWith("Meal deleted"); }
    catch (error) { $("#log-error").textContent = error.message; $("#log-error").hidden = false; }
    finally { busy(false); }
  });

  document.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => start({category: b.dataset.edit})));
  document.querySelectorAll("[data-add]").forEach(b => b.addEventListener("click", () => start({category: b.dataset.add})));
  document.querySelectorAll("[data-feel]").forEach(b => b.addEventListener("click", () => start({category: b.dataset.feel, focus: "feel"})));
  document.querySelectorAll("[data-log]").forEach(a => a.addEventListener("click", event => { event.preventDefault(); start(); }));
  document.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", () => {
    const choice = data.copies[Number(b.dataset.copy)];
    b.closest("details").open = false;
    start({category: choice.target, food: choice.food});
  }));
  document.addEventListener("click", event => {  // close the copy menu when tapping elsewhere
    const menu = $(".copy-menu");
    if (menu?.open && !menu.contains(event.target)) menu.open = false;
  });
  if (new URLSearchParams(location.search).get("log")) {
    history.replaceState(null, "", location.pathname + (pageDay !== data.today ? `?date=${pageDay}` : ""));
    start();
  }

  // Other info: the day's notes.
  const notesSheet = $("#notes-sheet");
  let notesVersion = Number($("#notes-form").dataset.version), notesOriginal = $("#day-notes").value;
  const notesDirty = () => notesSheet.open && $("#day-notes").value !== notesOriginal;
  const openNotes = sheetControls(notesSheet, notesDirty);
  $("#open-notes").addEventListener("click", () => { $("#notes-error").hidden = true; openNotes(); $("#day-notes").focus(); });
  $("#notes-form").addEventListener("submit", async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true; $("#notes-error").hidden = true;
    button.textContent = "Saving…";
    try {
      const text = $("#day-notes").value;
      const note = await api(`/api/days/${pageDay}/notes`, {method: "PUT", body: JSON.stringify({text, expected_version: notesVersion})});
      notesVersion = note.version; notesOriginal = text;
      $("#open-notes").textContent = text || "Sleep, stress, exercise, symptoms between meals, anything unusual…";
      $("#open-notes").classList.toggle("has-note", !!text);
      notesSheet.close(); toast("Notes saved");
    } catch (error) { showError("#notes-error", error); }
    finally { button.disabled = false; button.textContent = "Save notes"; }
  });

  $("#day-picker").addEventListener("change", event => { if (event.target.value) location.href = `/?date=${event.target.value}`; });
  window.addEventListener("beforeunload", event => { if (isDirty() || notesDirty()) { event.preventDefault(); event.returnValue = ""; } });
}
// Report: changes refresh the preview; printing gives a PDF through the device's print menu.
const reportForm = $("#report-form");
if (reportForm) {
  reportForm.querySelectorAll("input").forEach(input => input.addEventListener("change", () => {
    const start = reportForm.elements.start.value, end = reportForm.elements.end.value;
    if (start && end) reportForm.requestSubmit ? reportForm.requestSubmit() : reportForm.submit();
  }));
  $("#print-report")?.addEventListener("click", () => window.print());
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
// Garmin account sign-in: email and password, then a code if Garmin asks for one.
async function accountPost(url, form, button, done) {
  const status = $("#garmin-account-status");
  button.disabled = true; status.textContent = "Signing in to Garmin…";
  try { done(await api(url, {method:"POST", body:JSON.stringify(formBody(form))})); }
  catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
}
const accountSignedIn = () => {
  $("#garmin-account-status").textContent = "Signed in. Downloading the last 30 days again to add steps and stress…";
  startHistory({days:30}, null, $("#garmin-status"), () => location.reload());
};
$("#garmin-account-form")?.addEventListener("submit", event => {
  event.preventDefault();
  const form = event.currentTarget;
  accountPost("/api/garmin/account/login", form, event.submitter, result => {
    form.elements.password.value = "";
    if (result.needs_mfa) {
      form.hidden = true; $("#garmin-mfa-form").hidden = false; $("#garmin-code").focus();
      $("#garmin-account-status").textContent = "";
    } else accountSignedIn();
  });
});
$("#garmin-mfa-form")?.addEventListener("submit", event => {
  event.preventDefault();
  accountPost("/api/garmin/account/mfa", event.currentTarget, event.submitter, accountSignedIn);
});
$("#garmin-account-unlink")?.addEventListener("click", event => {
  if (!confirm("Sign out of your Garmin account? Steps and stress already synced stay; new days get daily totals only.")) return;
  garminPost("/api/garmin/account/unlink", {}, event.target, () => location.reload());
});
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

// Your day with meals: read the 15-minute slot under the pointer; meal tags switch the summary card.
function bindDayChart(section) {
  const combo = section?.querySelector(".combo"), readout = section?.querySelector(".combo-readout");
  if (!combo || !readout) return;
  const first = Number(section.dataset.start), span = Number(section.dataset.end) - first;
  const slots = new Map(JSON.parse(combo.dataset.slots || "[]").map(slot => [slot.time, slot]));
  const resting = readout.textContent, cursors = section.querySelectorAll(".tcursor");
  const pad = n => String(n).padStart(2, "0"), clock = m => `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`;
  function read(event) {
    const box = event.currentTarget.getBoundingClientRect();
    const minute = Math.min(first + span - 1, Math.max(first, Math.floor(first + (event.clientX - box.left) / box.width * span)));
    const start = minute - minute % 15, slot = slots.get(clock(start));
    const parts = [`${clock(start)}–${clock(start + 15)}`];
    if (!slot) parts.push("no readings");
    else {
      if (slot.hr != null) parts.push(`${slot.hr} bpm (${slot.hr_min}–${slot.hr_max})`);
      if (slot.stress != null) parts.push(`stress ${slot.stress}${slot.stress >= 76 ? " (high)" : ""}`);
      if (slot.steps != null) parts.push(`${slot.steps.toLocaleString("en-AU")} steps`);
      if (slot.body_battery != null) parts.push(`Body Battery ${slot.body_battery}`);
    }
    readout.textContent = parts.join(" · "); readout.classList.add("on");
    cursors.forEach(c => { c.hidden = false; c.style.left = `${(start + 7.5 - first) / span * 100}%`; });
  }
  function reset() { readout.textContent = resting; readout.classList.remove("on"); cursors.forEach(c => { c.hidden = true; }); }
  section.querySelectorAll(".lane-plot").forEach(plot => {
    plot.addEventListener("pointermove", read); plot.addEventListener("pointerdown", read); plot.addEventListener("pointerleave", reset);
  });
  section.querySelectorAll("[data-window]").forEach(tag => tag.addEventListener("click", () => {
    section.querySelectorAll("[data-window]").forEach(t => t.setAttribute("aria-pressed", String(t === tag)));
    section.querySelectorAll("[data-window-card]").forEach(card => { card.hidden = card.dataset.windowCard !== tag.dataset.window; });
  }));
}
bindDayChart($("#timeline"));
const health = $(".health-layout-root");
if (health && $("#health-data")) {
  const days = JSON.parse($("#health-data").textContent);
  let selected = Number(health.dataset.selected), sheetIndex = selected;
  const fmt = n => n == null ? "–" : Math.round(n).toLocaleString("en-AU");
  const hm = minutes => `${Math.floor(minutes / 60)}h ${String(Math.round(minutes % 60)).padStart(2, "0")}m`;
  const label = value => value ? String(value).replace(/_/g, " ").toLowerCase().replace(/^./, c => c.toUpperCase()) : "";
  const shortDate = iso => { const d = new Date(iso + "T00:00:00"); return d.toLocaleDateString("en-AU", {day:"numeric", month:"short"}); };
  function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; }
  function setValue(node, value, unit) {
    if (!node) return;  // a single day shows 15-minute charts instead
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
  function select(i) { selected = i; show(i); }
  document.querySelectorAll(".col").forEach(col => {
    col.addEventListener("click", () => select(Number(col.dataset.i)));
    col.addEventListener("mouseenter", () => show(Number(col.dataset.i)));
  });
  document.querySelectorAll(".plot").forEach(plot => plot.addEventListener("mouseleave", () => show(selected)));

  // Day sheet ------------------------------------------------------------------
  const sheet = $("#day-sheet");
  function tile(title, value, unit, sub, muted = false) {
    const box = el("div", "tile outline"), main = el("span", "tile-value" + (muted ? " muted-value" : ""), String(value));
    if (unit) main.append(el("small", null, " " + unit));
    box.append(el("span", "tile-label", title), main);
    if (sub) box.append(el("span", "tile-sub", sub));
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
    if ($("#sheet-detail")) $("#sheet-detail").href = `/health?start=${day.iso}&end=${day.iso}`;
    $("#sheet-meals-sub").textContent = `What you logged in the diary on ${shortDate(day.iso)}`;
    const prev = days[i - 1], next = days[i + 1];
    $("#sheet-prev").disabled = !prev; $("#sheet-next").disabled = !next;
    $("#sheet-prev").setAttribute("aria-label", prev ? `Previous day: ${prev.long}` : "Previous day");
    $("#sheet-next").setAttribute("aria-label", next ? `Next day: ${next.long}` : "Next day");
  }
  function openSheet(i) { fillSheet(i); select(i); if (!sheet.open) { sheet.showModal(); document.body.classList.add("sheet-open"); } }
  sheet.addEventListener("close", () => document.body.classList.remove("sheet-open"));
  sheet.addEventListener("click", event => { if (event.target === sheet) sheet.close(); });
  $("[data-close-sheet]").addEventListener("click", () => sheet.close());
  $("#sheet-prev").addEventListener("click", () => openSheet(sheetIndex - 1));
  $("#sheet-next").addEventListener("click", () => openSheet(sheetIndex + 1));
  $("#sel-view").addEventListener("click", event => { event.preventDefault(); openSheet(selected); });
  document.querySelectorAll("[data-open-day]").forEach(link => link.addEventListener("click", event => { event.preventDefault(); openSheet(Number(link.dataset.openDay)); }));
  $("#show-all-days")?.addEventListener("click", event => { $(".log-section").classList.add("show-more"); event.target.hidden = true; });
}
document.querySelectorAll("[data-health-sync]").forEach(button => button.addEventListener("click", () => {
  const layout = $(".health-layout-root"), status = $("#health-sync-status"), alert = $("#health-alert");
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
if ($(".health-layout-root") && $("[data-health-sync]")) {
  // Pick up a download started elsewhere (Settings, or another tab).
  api("/api/garmin/history").then(job => { if (job.state === "running") watchHistory($("#health-sync-status"), () => location.reload()); }).catch(() => {});
}

// Stress alerts: subscribe this phone to Web Push (Settings).
(async function pushSetup() {
  const box = $("#push-controls");
  if (!box) return;
  const status = $("#push-status");
  if (!("serviceWorker" in navigator && "PushManager" in window && "Notification" in window)) {
    const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
    status.textContent = ios ? "On iPhone, add the diary to your Home Screen (Share → Add to Home Screen), open it from there, then turn alerts on here."
                             : "This browser can’t show notifications from the diary.";
    return;
  }
  const device = /Android/.test(navigator.userAgent) ? "Android phone" : /iPhone|iPad/.test(navigator.userAgent) ? "iPhone" : "Browser";
  const key = Uint8Array.from(atob(box.dataset.key.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));
  const save = sub => api("/api/push/subscribe", {method: "POST", body: JSON.stringify({...sub.toJSON(), device})});
  let reg, sub;
  try { reg = await navigator.serviceWorker.ready; sub = await reg.pushManager.getSubscription(); }
  catch { status.textContent = "Couldn’t start notifications in this browser."; return; }
  const show = message => {
    $("#push-on").hidden = !!sub; $("#push-test").hidden = $("#push-off").hidden = !sub;
    status.textContent = message ?? (sub ? "Alerts are on for this phone."
      : Notification.permission === "denied" ? "Notifications are blocked for the diary. Allow them in your phone’s settings for this app, then try again." : "");
  };
  box.hidden = false;
  if (sub) save(sub).catch(() => {});  // keep the diary's copy current
  show();
  $("#push-on").addEventListener("click", async event => {
    event.target.disabled = true;
    try {
      if (await Notification.requestPermission() !== "granted") { show(); return; }
      const options = {userVisibleOnly: true, applicationServerKey: key};
      try { sub = await reg.pushManager.subscribe(options); }
      catch (error) {  // an old subscription made with a different key: replace it
        const old = await reg.pushManager.getSubscription();
        if (!old) throw error;
        await old.unsubscribe(); sub = await reg.pushManager.subscribe(options);
      }
      await save(sub);
      show("Alerts are on for this phone. Send a test alert to check it works.");
    } catch (error) { sub = null; show(error.message || "Couldn’t turn alerts on."); }
    finally { event.target.disabled = false; }
  });
  $("#push-test").addEventListener("click", async event => {
    event.target.disabled = true;
    try {
      const result = await api("/api/push/test", {method: "POST"});
      show(result.delivered ? "Test alert sent. It should appear in a few seconds." : "No device accepted the test alert. Turn alerts off and on again.");
    } catch (error) { show(error.message); }
    finally { event.target.disabled = false; }
  });
  $("#push-off").addEventListener("click", async event => {
    event.target.disabled = true;
    try {
      await api("/api/push/unsubscribe", {method: "POST", body: JSON.stringify({endpoint: sub.endpoint})});
      await sub.unsubscribe(); sub = null; show("Alerts are off for this phone.");
    } catch (error) { show(error.message); }
    finally { event.target.disabled = false; }
  });
})();

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
