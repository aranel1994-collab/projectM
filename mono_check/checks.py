"""Детерміновані результати правил і маршрути."""

from datetime import date
import json
from pathlib import Path
import re

from .pdf import normalize, valid_date


ROUTES = {
    "STANDARD": "Стандартна обробка", "MANUAL_REVIEW": "Ручна перевірка", "ESCALATE": "Ескалація",
}
REASONS = {
    "ENTITY_TERMINATED": "У виписці зафіксовано припинення юридичної особи",
    "ENTITY_STATUS_REVIEW": "Стан юридичної особи потребує звірки з ЄДР",
    "SCAN_SOURCE": "Висновок залежить від сторінки-зображення",
    "UNSUPPORTED_ENTITY": "Форма юрособи поза підтримуваним сценарієм",
    "COMPLEX_STRUCTURE": "Структура власності потребує окремого аналізу",
    "UNKNOWN_FORM": "Формат виписки потребує уточнення",
    "MODEL_ONLY_EVIDENCE": "Модель знайшла цитату, але формат поля потребує ручної звірки",
    "UNGROUNDED_VALUES": "Критичному полю бракує підтвердженого доказу",
    "CRITICAL_FIELD": "Критичні дані неповні або суперечливі",
    "REGISTRATION_DATE_CONFLICT": "Дати реєстрації, формування виписки або перевірки суперечать одна одній",
    "CASE_ENTITY_MISMATCH": "Код у PDF відрізняється від коду заявки",
    "DOCUMENT_CODE_CONFLICT": "У розділі компанії є різні власні коди",
    "RULE_VERSION_UNAVAILABLE": "На дату перевірки немає придатної версії правил",
}
REGISTRY = Path(__file__).with_name("rules.json")
CORE = {"name", "code", "address", "registration_date", "registration_number", "management"}
TERMINATED = re.compile(r"^(?:юридичну особу )?припинено\b|^державн\w+ реєстраці\w+ припинення\b")
ACTIVE = {"зареєстровано", "зареєстрована", "діюча", "діючий", "діє"}
NO_TERMINATION = re.compile(r"^(?:юридична особа )?не перебуває в (?:процесі|стані) припинення[. ]*$")


def load_rules(check_date):
    versions = json.loads(REGISTRY.read_text(encoding="utf-8"))["versions"]
    return max(
        (version for version in versions if date.fromisoformat(version["effective_from"]) <= check_date),
        default=None, key=lambda version: (version["effective_from"], version["id"]),
    )


def statement_company(fields, form):
    names = fields["name"]["findings"]
    if form != "statement":
        return "unknown"
    if any(re.match(r"^(?:товариство з обмеженою відповідальністю|тов\b)", normalize(name["value"])) for name in names):
        return "llc"
    return "other" if names else "unknown"


def result(rule, version, fields, status, summary, action=None):
    proof = [
        {"field": field, "label": fields[field]["label"], **item}
        for field in rule["fields"] for item in fields[field]["findings"]
    ]
    basis = [
        {**version["sources"][source], "pinpoint": pinpoint}
        for source, pinpoint in rule["basis"]
    ]
    return {
        "id": rule["id"], "group": rule["group"], "label": rule["label"],
        "status": status, "summary": summary, "action": action if action is not None else rule["action"],
        "basis": basis, "policy": rule.get("policy"), "evidence": proof,
    }


def checked_date(field):
    return valid_date(field["findings"][0]["value"]) if field["state"] == "present" else None


def entity_status(fields, reasons):
    status = fields["company_status"]
    termination = fields["termination"]
    if any(TERMINATED.search(normalize(item["value"])) for item in status["findings"] if item["source"] == "text"):
        reasons.add("ENTITY_TERMINATED")
        return "FAIL", "У розділі стану самої компанії зафіксовано припинення.", None
    has_termination = termination["state"] not in {"section_absent", "explicit_absent"} and not (
        termination["state"] == "present" and all(NO_TERMINATION.fullmatch(normalize(item["value"])) for item in termination["findings"])
    )
    if has_termination:
        reasons.add("ENTITY_STATUS_REVIEW")
        return "REVIEW", "У виписці є розділ про припинення, який потрібно звірити з ЄДР.", None
    if status["state"] == "present" and normalize(status["findings"][0]["value"]).rstrip(".") in ACTIVE:
        return "PASS", "У виписці наведено діючий стан. Актуальний стан перевіряється за ЄДР.", ""
    reasons.add("ENTITY_STATUS_REVIEW")
    return "REVIEW", "Діючий стан у виписці не підтверджено. Звірте стан у ЄДР.", None


def pdf_results(rule, fields, reasons, claimed_code, check_date, expanded, form):
    key = rule["id"]
    states = [fields[field]["state"] for field in rule["fields"]]
    if key == "DOC-08":
        return entity_status(fields, reasons)
    if key == "DOC-03":
        registered = checked_date(fields["registration_date"])
        issued = checked_date(fields["issued_date"])
        if registered and (registered > check_date or (issued and registered > issued)):
            reasons.add("REGISTRATION_DATE_CONFLICT")
            return "REVIEW", "Дата реєстраційного запису пізніша за дату формування виписки або перевірки.", None
    if key in {"DOC-01", "DOC-02", "DOC-03", "DOC-04"}:
        if all(state == "present" for state in states):
            return "PASS", "Потрібні відомості знайдено в текстовому розділі виписки.", ""
        if key == "DOC-02" and states == ["conflicting"]:
            return "FAIL", "Виписка містить різні коди в розділі самої компанії.", None
        return "REVIEW", "Для частини відомостей потрібне підтвердження.", None
    if key == "DOC-05":
        owners = fields["owners"]
        ubo = fields["ubo"]
        if expanded and any(f["state"] != "present" for f in (owners, ubo)):
            reasons.add("CRITICAL_FIELD")
            return "REVIEW", "У розширеній формі бракує підтверджених відомостей про власників або КБВ.", None
        if form == "statement" and owners["state"] == ubo["state"] == "section_absent":
            return "NOT_APPLICABLE", "У цьому шаблоні немає розділів власності для звірки часток. Встановлення КБВ залишається подальшою дією.", ""
        if owners["state"] == "present":
            shares = re.findall(r"(?<![\d,\.])(\d{1,3}(?:[.,]\d+)?)\s*%", " ".join(item["value"] for item in owners["findings"]))
            if shares and abs(sum(float(s.replace(",", ".")) for s in shares) - 100) > 0.1:
                reasons.add("CRITICAL_FIELD")
                return "REVIEW", "Сума зазначених часток відрізняється від 100%. Потрібно звірити повноту й значення.", None
        return "NOT_CHECKED", "У звіті наведено наявні відомості, а встановлення структури власності показано серед подальших дій.", None
    if key == "DOC-06":
        issued = checked_date(fields["issued_date"])
        if issued:
            if issued > check_date:
                reasons.add("CRITICAL_FIELD")
                return "REVIEW", "Дата формування пізніша за дату перевірки.", None
            return "NOT_CHECKED", f"На дату перевірки виписці {(check_date - issued).days} дн. Поріг давності не задано.", None
        return "NOT_CHECKED", "Дату формування не підтверджено з PDF.", None
    values = {item["value"] for item in fields["code"]["findings"] if item["source"] == "text"}
    if len(values) > 1:
        reasons.add("DOCUMENT_CODE_CONFLICT")
        return "FAIL", "Два різні коди наведено як код однієї компанії.", "Передайте обидва докази на поглиблений розгляд."
    if not claimed_code:
        return "NOT_CHECKED", "Код від заявки не надано.", "Підтвердьте, що виписка належить заявнику."
    if fields["code"]["state"] != "present":
        return "REVIEW", "Зіставлення потребує підтвердженого коду компанії в PDF.", None
    if claimed_code not in values:
        reasons.add("CASE_ENTITY_MISMATCH")
        return "REVIEW", "Заявлений код і код компанії в PDF різні.", "Уточніть код заявки або попросіть виписку для потрібної компанії."
    return "PASS", "Код компанії в PDF збігається із заявленим.", ""


def evaluate(fields, form, expanded, rejected, claimed_code, check_date, pages):
    reasons = set()
    version = load_rules(check_date)
    scanned = any(page.kind == "scan" for page in pages)
    if form != "statement":
        reasons.add("UNKNOWN_FORM")
    if statement_company(fields, form) == "other":
        reasons.add("UNSUPPORTED_ENTITY")
    if expanded and any(
        re.search(r"(?:іноземн|юридичн|товариство|\bтов\b)", normalize(item["value"]))
        for item in fields["owners"]["findings"]
    ):
        reasons.add("COMPLEX_STRUCTURE")
    if scanned and any(fields[field]["state"] != "present" for field in CORE | {"issued_date"}):
        reasons.add("SCAN_SOURCE")
    if any(fields[field]["state"] == "text_model" for field in fields):
        reasons.add("MODEL_ONLY_EVIDENCE")
    if any(fields[field]["state"] not in {"present", "text_model"} for field in CORE):
        reasons.add("CRITICAL_FIELD")
    if any(fields[field]["state"] == "ungrounded" for field in CORE) or CORE.intersection(rejected):
        reasons.add("UNGROUNDED_VALUES")
    if not version:
        reasons.add("RULE_VERSION_UNAVAILABLE")
    results = []
    if version:
        for rule in version["rules"]:
            if rule["group"] == "pdf":
                state, summary, action = pdf_results(rule, fields, reasons, claimed_code, check_date, expanded, form)
            else:
                state, summary, action = "NOT_CHECKED", "Потребує дії за додатковим джерелом.", None
            results.append(result(rule, version, fields, state, summary, action))
    if reasons & {"ENTITY_TERMINATED", "DOCUMENT_CODE_CONFLICT"}:
        route = "ESCALATE"
    else:
        route = "MANUAL_REVIEW" if reasons else "STANDARD"
    ordered_reasons = [{"code": code, "label": REASONS[code]} for code in REASONS if code in reasons]
    return route, ordered_reasons, results, version
