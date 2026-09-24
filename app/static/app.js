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
  function snapshot() { return JSON.stringify(Object.fromEntries(new FormData(form))); }
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
      // Read afresh so ChatGPT edits appear before the form is opened.
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
  if (!confirm("Disconnect all ChatGPT and MCP connections? You can connect again later.")) return;
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
