"use strict";

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (className) el.className = className;
  return el;
};

const ruleStates = {
  PASS: "Перевірено", REVIEW: "Потрібна перевірка", FAIL: "Виявлено суперечність",
  NOT_APPLICABLE: "Не застосовується", NOT_CHECKED: "Не перевірено",
};
const fieldStates = {
  present: "Знайдено", section_absent: "Розділу немає", empty: "Поле порожнє",
  explicit_absent: "Відомості відсутні", unreadable: "Не прочитано", ungrounded: "Доказ не підтверджено",
  conflicting: "Суперечливі значення", scan: "Прочитано зі скану",
};
const actions = {
  next: "Передати далі", request: "Запросити документ", escalate: "Ескалація",
};
let report;
let chosenPage = 1;
let activeButton;
let busy = false;

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
  if (activeButton) activeButton.setAttribute("aria-pressed", "false");
  activeButton = button;
  if (button) button.setAttribute("aria-pressed", "true");
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
    : "Сторінка " + number + " · вибери доказ у звіті";
  $("active-proof").textContent = description;
  for (const pager of $("pager").children) {
    if (Number(pager.dataset.page) === chosenPage) pager.setAttribute("aria-current", "page");
    else pager.removeAttribute("aria-current");
  }
  if (button && matchMedia("(max-width: 670px)").matches) {
    document.querySelector(".pdf-card").scrollIntoView({block: "start"});
  }
}

function proofButton(evidence, index, total) {
  const description = evidence.source === "scan" ? "Зображення" : "Доказ";
  const suffix = total > 1 ? " · " + (index + 1) : "";
  const button = node("button", description + " · с. " + evidence.page + suffix, "proof");
  button.type = "button";
  button.setAttribute("aria-pressed", "false");
  button.setAttribute("aria-label", description + " на сторінці " + evidence.page + " · " + evidence.label + suffix);
  button.addEventListener("click", () => setPage(evidence.page, evidence, button));
  return button;
}

function renderFact(field) {
  const fact = node("article", "", "fact");
  const title = node("div", "", "fact-title");
  title.append(node("h3", field.label), pill(fieldStates[field.state], field.state === "present" ? "pass" : field.state === "conflicting" ? "fail" : "other"));
  fact.append(title);
  field.findings.forEach((item, index) => {
    fact.append(node("p", item.value), proofButton({...item, label: field.label}, index, field.findings.length));
  });
  return fact;
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

function renderReport(data) {
  report = data;
  $("report").dataset.route = data.route;
  $("report").hidden = false;
  const name = data.fields.name.findings.length ? data.fields.name.findings[0].value : "Виписка потребує уточнення";
  const code = data.fields.code.findings.map((item) => item.value).join(" / ");
  const head = $("report-head");
  head.replaceChildren(pill(data.route_label, data.route), node("h2", name));
  const facts = [data.form, code ? "ЄДРПОУ " + code : null, data.claimed_code ? "Заявлено " + data.claimed_code : null].filter(Boolean);
  head.append(node("p", facts.join(" · "), "report-meta"));
  const versions = [data.mode_label, data.check_date, data.rule_version ? "Правила " + data.rule_version : "Версія правил відсутня", data.model, data.prompt_version ? "Промпт " + data.prompt_version : null, "Схема " + data.schema_version].filter(Boolean);
  head.append(node("p", versions.join(" · "), "report-meta"));
  if (data.reasons.length) {
    const reasons = node("ul", "", "reasons");
    reasons.append(...data.reasons.map((reason) => node("li", reason.label)));
    head.append(reasons);
  }

  const primaryFields = Object.values(data.fields).slice(0, 7);
  const otherFields = Object.values(data.fields).slice(7);
  const more = node("details");
  more.append(node("summary", "Власність, стан і види діяльності"), ...otherFields.map(renderFact));
  $("facts").replaceChildren(...primaryFields.map(renderFact), more);
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
  const permitted = data.route === "STANDARD" ? ["next", "request", "escalate"] : data.route === "ESCALATE" ? ["escalate"] : ["request", "escalate"];
  $("decision-options").replaceChildren(...permitted.map((action, index) => {
    const label = node("label");
    const radio = node("input");
    radio.type = "radio";
    radio.name = "decision";
    radio.value = action;
    radio.checked = index === 0;
    label.append(radio, document.createTextNode(actions[action]));
    return label;
  }));
  $("comment").value = "";
  $("decision-feedback").textContent = "";
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

$("decision").addEventListener("submit", (event) => {
  event.preventDefault();
  const decision = new FormData(event.currentTarget).get("decision");
  const comment = $("comment").value.trim();
  const extra = comment ? " · " + comment : "";
  $("decision-feedback").textContent = "Зафіксовано в поточному перегляді · " + actions[decision] + extra;
});

$("mode").addEventListener("change", () => { $("live-note").hidden = $("mode").value !== "live"; });

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
