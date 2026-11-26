"use strict";

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (className) el.className = className;
  return el;
};

const ruleStates = {
  PASS: "Перевірено", REVIEW: "Потрібна перевірка", FAIL: "Виявлено проблему",
  NOT_APPLICABLE: "Не застосовується", NOT_CHECKED: "Не перевірено",
};
const fieldStates = {
  present: "Знайдено", section_absent: "Розділу немає", empty: "Поле порожнє",
  explicit_absent: "Відомості відсутні", unreadable: "Не прочитано", ungrounded: "Доказ не підтверджено",
  conflicting: "Суперечливі значення", scan: "Прочитано зі скану",
  text_model: "Знайдено моделлю, шаблон не підтверджено",
};
const actions = {
  next: "Передати далі", request: "Запросити документ", escalate: "Ескалація",
};
let report;
let chosenPage = 1;
let activeButton;
let busy = false;
let historyRequest = 0;
const historyDate = new Intl.DateTimeFormat("uk-UA", {dateStyle: "short", timeStyle: "short"});

function pill(text, status) {
  return node("span", text, "pill " + status.toLowerCase());
}

function showMessage(message, error = false) {
  $("message").textContent = message;
  $("message").classList.toggle("error", error);
}

function setBusy(value) {
  busy = value;
  $("submit").disabled = value;
  document.querySelectorAll(".example").forEach((button) => { button.disabled = value; });
}

function setPage(number, evidence, button) {
  const page = report.pages.find((item) => item.number === number);
  if (!page) return;
  chosenPage = number;
  $("sheet").src = "data:image/jpeg;base64," + page.image;
  $("sheet").alt = "Сторінка " + number + " документа";
  $("view-kind").textContent = page.kind === "scan" ? "Зображення" : "Текстовий шар";
  $("marks").replaceChildren();
  if (activeButton) {
    activeButton.setAttribute("aria-pressed", "false");
    activeButton.closest(".fact-row")?.classList.remove("selected");
  }
  activeButton = button;
  if (button) {
    button.setAttribute("aria-pressed", "true");
    button.closest(".fact-row")?.classList.add("selected");
  }
  for (const rect of (evidence && evidence.positions) || []) {
    const marker = node("span", "", "mark");
    marker.style.left = rect.x + "%";
    marker.style.top = rect.y + "%";
    marker.style.width = rect.w + "%";
    marker.style.height = rect.h + "%";
    $("marks").append(marker);
  }
  const description = evidence
    ? evidence.label + " · с. " + number + " · " + evidence.fragment
    : "Сторінка " + number + " · обери рядок або доказ у звіті";
  $("active-proof").textContent = description;
  for (const pager of $("pager").children) {
    if (Number(pager.dataset.page) === chosenPage) pager.setAttribute("aria-current", "page");
    else pager.removeAttribute("aria-current");
  }
  if (button && matchMedia("(max-width: 670px)").matches) {
    document.querySelector(".pdf-card").scrollIntoView({block: "start"});
  }
}

function proofButton(evidence, index, total, compact = false) {
  const description = {scan: "Зображення", text_model: "Цитата моделі"}[evidence.source] || "Доказ";
  const suffix = total > 1 ? " · " + (index + 1) : "";
  const pageLabel = "с. " + evidence.page + suffix;
  const button = node("button", compact ? pageLabel : description + " · " + pageLabel, "proof");
  button.type = "button";
  button.setAttribute("aria-pressed", "false");
  button.setAttribute("aria-label", description + " на сторінці " + evidence.page + " · " + evidence.label + suffix);
  button.addEventListener("click", () => setPage(evidence.page, evidence, button));
  return button;
}

function renderFact([key, field]) {
  const row = node("tr", "", "fact-row");
  row.dataset.field = key;
  row.setAttribute("role", "row");
  const label = node("th", field.label);
  label.scope = "row";
  label.setAttribute("role", "rowheader");
  const values = node("td");
  const status = node("td");
  const proofs = node("td");
  for (const cell of [values, status, proofs]) cell.setAttribute("role", "cell");
  const state = report.mode === "replay" && field.state === "scan" ? "Підготовлені дані зі скану" : fieldStates[field.state];
  status.append(pill(state, field.state === "present" ? "pass" : field.state === "conflicting" ? "fail" : "other"));
  const stack = node("div", "", "fact-proofs");
  field.findings.forEach((item, index) => {
    values.append(node("div", item.value));
    stack.append(proofButton({...item, label: field.label}, index, field.findings.length, true));
  });
  if (stack.firstElementChild) {
    proofs.append(stack);
    row.classList.add("has-proof");
    row.addEventListener("click", (event) => {
      if (!event.target.closest("button")) stack.firstElementChild.click();
    });
  } else {
    values.append(node("span", "—", "muted"));
    proofs.append(node("span", "—", "muted"));
  }
  row.append(label, values, status, proofs);
  return row;
}

function factTable(fields, label) {
  const table = node("table", "", "facts-table");
  table.setAttribute("role", "table");
  table.setAttribute("aria-label", label);
  const headings = table.createTHead().insertRow();
  for (const title of ["Реквізит", "Значення з PDF", "Статус", "Доказ"]) {
    const heading = node("th", title);
    heading.scope = "col";
    headings.append(heading);
  }
  const body = table.createTBody();
  body.setAttribute("role", "rowgroup");
  body.append(...fields.map(renderFact));
  return table;
}

function renderRule(rule) {
  const check = node("article", "", "check");
  check.dataset.rule = rule.id;
  const title = node("div", "", "check-title");
  title.append(node("h3", rule.label), pill(ruleStates[rule.status], rule.status));
  check.append(title, node("p", rule.summary, "muted"));
  if (rule.action) check.append(node("p", rule.action));
  if (rule.llm_note) {
    const extra = document.createElement("details");
    extra.append(node("summary", "Пояснення моделі"), node("p", rule.llm_note));
    check.append(extra);
  }
  for (const [index, evidence] of rule.evidence.entries()) {
    check.append(proofButton(evidence, index, rule.evidence.length));
  }
  if (rule.basis.length || rule.policy) {
    const basis = node("div", rule.policy || "", "basis");
    for (const reference of rule.basis) {
      const link = node("a", reference.title + " · " + reference.pinpoint + " · " + reference.edition);
      link.href = reference.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      basis.append(link);
    }
    check.append(basis);
  }
  return check;
}

function renderHistory(entries) {
  $("history-message").textContent = entries.length ? "" : "Поки немає збережених перевірок.";
  $("history-message").hidden = entries.length > 0;
  $("history-table").hidden = entries.length === 0;
  $("history-rows").replaceChildren(...entries.map((entry) => {
    const row = node("tr");
    row.dataset.historyId = entry.id;
    row.addEventListener("click", () => openHistory(entry.id));
    const company = node("td");
    const open = node("button", entry.company || "Назву не встановлено", "history-company");
    open.type = "button";
    open.setAttribute("aria-label", "Відкрити перевірку " + (entry.company || "№" + entry.id));
    company.append(open);
    if (entry.code) company.append(node("small", entry.code));
    const route = node("td");
    route.append(pill(entry.route_label, entry.route));
    const decision = node("td", actions[entry.decision] || "Очікує дії");
    if (entry.comment) decision.append(node("small", entry.comment));
    row.append(node("td", historyDate.format(new Date(entry.created_at))), company, route, node("td", entry.mode_label), decision);
    return row;
  }));
}

async function refreshHistory() {
  const request = ++historyRequest;
  try {
    const response = await fetch("/api/history");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    if (request === historyRequest) renderHistory(data.entries);
  } catch (error) {
    if (request !== historyRequest) return;
    $("history-message").textContent = "Не вдалося завантажити історію. Онови сторінку.";
    $("history-message").hidden = false;
  }
}

function historyBackLink() {
  const link = node("a", "До історії перевірок", "report-meta");
  link.href = "#history";
  return link;
}

async function openHistory(entryId) {
  if (busy) return;
  setBusy(true);
  $("history").setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/api/history/" + entryId);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не вдалося відкрити перевірку.");
    if (data.report) renderReport(data.report, data.entry);
    else renderHistorySummary(data.entry);
    $("history-message").hidden = true;
    $("report-head").tabIndex = -1;
    $("report-head").focus({preventScroll: true});
  } catch (error) {
    $("history-message").textContent = error.message;
    $("history-message").hidden = false;
  } finally {
    $("history").removeAttribute("aria-busy");
    setBusy(false);
  }
}

function renderHistorySummary(entry) {
  report = null;
  $("report").dataset.route = entry.route;
  $("report").hidden = false;
  $("report-grid").hidden = true;
  const head = $("report-head");
  head.replaceChildren(
    pill(entry.route_label, entry.route),
    node("h2", entry.company || "Назву не встановлено"),
    node("p", [entry.code && "ЄДРПОУ " + entry.code, entry.mode_label, entry.check_date, entry.rule_version].filter(Boolean).join(" · "), "report-meta"),
    node("p", "Детальний звіт для цієї перевірки ще не зберігався.", "report-meta"),
  );
  if (entry.decision) head.append(node("p", [actions[entry.decision], entry.comment].filter(Boolean).join(" · "), "report-meta"));
  head.append(historyBackLink());
  head.scrollIntoView({block: "start"});
}

function renderReport(data, entry = null) {
  report = data;
  $("report").dataset.route = data.route;
  $("report").hidden = false;
  $("report-grid").hidden = false;
  const name = data.fields.name.findings.length ? data.fields.name.findings[0].value : "Виписка потребує уточнення";
  const code = data.fields.code.findings.map((item) => item.value).join(" / ");
  const head = $("report-head");
  head.replaceChildren(pill(data.route_label, data.route), node("h2", name));
  const facts = [data.form, code ? "ЄДРПОУ " + code : null, data.claimed_code ? "Заявлено " + data.claimed_code : null].filter(Boolean);
  head.append(node("p", facts.join(" · "), "report-meta"));
  const versions = [data.mode_label, data.check_date, data.rule_version ? "Правила " + data.rule_version : "Версія правил відсутня", data.model, data.prompt_version ? "Промпт " + data.prompt_version : null, "Схема " + data.schema_version].filter(Boolean);
  head.append(node("p", versions.join(" · "), "report-meta"));
  if (entry) {
    head.append(node("p", "Запис від " + historyDate.format(new Date(entry.created_at)), "report-meta"));
    if (entry.restored_at) head.append(node("p", "Деталі відновлено повторною перевіркою PDF.", "report-meta"));
  }
  head.append(historyBackLink());
  if (data.reasons.length) {
    const reasons = node("ul", "", "reasons");
    reasons.append(...data.reasons.map((reason) => node("li", reason.label)));
    head.append(reasons);
  }

  const fields = Object.entries(data.fields);
  const more = node("details", "", "facts-more");
  more.append(node("summary", "Власність, стан і види діяльності"),
    factTable(fields.slice(7), "Власність, стан і види діяльності"));
  $("facts").replaceChildren(factTable(fields.slice(0, 7), "Основні реквізити з виписки"), more);
  const own = data.results.filter((rule) => rule.group === "pdf");
  $("checks").replaceChildren(...own.map(renderRule));
  if (!own.length) $("checks").append(node("p", "Виберіть дату, для якої опубліковано набір правил.", "muted"));
  $("next").replaceChildren();
  for (const [group, heading] of [["documents", "Документи й анкета"], ["external", "Зовнішні джерела"]]) {
    const rest = data.results.filter((rule) => rule.group === group);
    if (rest.length) $("next").append(node("h3", heading, "group-title"), ...rest.map(renderRule));
  }

  $("pager").replaceChildren(...data.pages.map((page) => {
    const button = node("button", String(page.number));
    button.type = "button";
    button.dataset.page = page.number;
    button.setAttribute("aria-label", "Сторінка " + page.number);
    button.addEventListener("click", () => setPage(page.number));
    return button;
  }));
  $("decision-options").replaceChildren(...data.permitted_actions.map((action, index) => {
    const label = node("label");
    const radio = node("input");
    radio.type = "radio";
    radio.name = "decision";
    radio.value = action;
    radio.checked = entry && entry.decision ? entry.decision === action : index === 0;
    label.append(radio, document.createTextNode(actions[action]));
    return label;
  }));
  $("comment").value = entry && entry.comment ? entry.comment : "";
  $("decision-feedback").textContent = entry && entry.decision ? "Збережено в історії · " + actions[entry.decision] : "";
  $("decision-feedback").classList.remove("error");
  setPage(1);
  head.scrollIntoView({block: "start"});
}

function encode(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(reader.result.split(",")[1]));
    reader.addEventListener("error", () => reject(new Error("Не вдалося прочитати файл.")));
    reader.readAsDataURL(file);
  });
}

async function run(payload) {
  if (busy) return;
  $("report").hidden = true;
  setBusy(true);
  showMessage("Обробляємо PDF…");
  try {
    const response = await fetch("/api/analyze", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не вдалося завершити перевірку.");
    renderReport(data);
    showMessage("");
    await refreshHistory();
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    setBusy(false);
  }
}

$("entry").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const file = $("file").files[0];
  if (!file || file.size > 8 * 1024 * 1024) {
    showMessage("Оберіть один PDF до 8 МБ.", true);
    return;
  }
  try {
    const pdf = await encode(file);
    await run({pdf, mode: $("mode").value, claimed_code: $("code").value, date: $("date").value});
  } catch (error) {
    showMessage(error.message, true);
  }
});

$("decision").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !report) return;
  const button = event.currentTarget.querySelector("button[type=submit]");
  if (button.disabled) return;
  const historyId = report.history_id;
  const decision = new FormData(event.currentTarget).get("decision");
  const comment = $("comment").value.trim();
  const feedback = $("decision-feedback");
  button.disabled = true;
  feedback.classList.remove("error");
  try {
    const response = await fetch("/api/decision", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({history_id: historyId, decision, comment}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не вдалося зберегти дію.");
    if (report.history_id === historyId) feedback.textContent = "Збережено в історії · " + actions[decision];
    await refreshHistory();
  } catch (error) {
    if (report.history_id !== historyId) return;
    feedback.textContent = error.message;
    feedback.classList.add("error");
  } finally {
    button.disabled = false;
  }
});

$("mode").addEventListener("change", () => { $("live-note").hidden = $("mode").value !== "live"; });

refreshHistory();

fetch("/api/config").then((response) => response.json()).then((config) => {
  const mode = $("mode").querySelector("[value=live]");
  mode.disabled = !config.live_available;
  if (!config.live_available) mode.textContent = "Живий виклик · потрібен ключ API";
  config.examples.forEach((sample, index) => {
    const button = node("button", "", "example");
    button.type = "button";
    button.dataset.example = sample.id;
    button.append(node("strong", sample.title), node("span", sample.description));
    button.addEventListener("click", () => run({
      example: sample.id, mode: $("mode").value === "live" ? "live" : "replay", claimed_code: sample.claimed_code, date: $("date").value || "2026-11-24",
    }));
    $(index < 3 ? "main-examples" : "more-examples").append(button);
  });
}).catch(() => showMessage("Не вдалося завантажити приклади. Оновіть сторінку.", true));
