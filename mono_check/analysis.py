"""Один запуск перевірки та звіт для працівника."""

import base64
from datetime import date
import re

from .checks import ROUTES, evaluate
from .extraction import (
    PROMPT_VERSION, SCHEMA_VERSION, explain, live_findings,
    local_findings, model_name, replay_findings, verify,
)
from .pdf import DocumentError, open_pdf, page_content, page_jpeg, sections_from


MODE_LABELS = {
    "live": "Живий виклик моделі",
    "replay": "Відтворення підготовленого прикладу",
    "local": "Локальне читання тексту",
}


def analyze(data, mode="local", claimed_code="", check_date=None, include_pages=True, include_explanation=True):
    if mode not in MODE_LABELS:
        raise DocumentError("Виберіть доступний режим запуску.")
    if not isinstance(claimed_code, str) or (claimed_code.strip() and not re.fullmatch(r"\d{8}", claimed_code.strip(), re.ASCII)):
        raise DocumentError("Заявлений ЄДРПОУ має містити рівно 8 цифр.")
    claimed_code = claimed_code.strip()
    try:
        check_date = date.fromisoformat(check_date) if check_date else date.today()
    except (ValueError, TypeError):
        raise DocumentError("Вкажіть коректну дату перевірки.") from None
    with open_pdf(data) as document:
        pages = page_content(document)
        form, expanded, sections = sections_from(pages)
        model = model_usage = None
        if mode == "live":
            response = live_findings(document, pages)
            raw, model, model_usage = response.data, response.model, response.usage
        elif mode == "replay":
            raw = replay_findings(data, len(pages))
        else:
            raw = local_findings(sections)
        if form == "unknown" and any(page.kind == "scan" for page in pages):
            form_label = "Формат уточнюється за зображенням"
        elif form == "statement":
            form_label = "Виписка з ЄДР"
        elif form == "extract":
            form_label = "Витяг з ЄДР"
        else:
            form_label = "Нерозпізнаний формат"
        fields, rejected = verify(document, pages, sections, raw)
        route, reasons, results, version = evaluate(fields, form, expanded, rejected, claimed_code, check_date, pages)
        notes = explain(results) if mode == "live" and include_explanation else {}
        for result in results:
            result["llm_note"] = notes.get(result["id"])
        images = [
            {"number": page.number, "kind": page.kind, "image": base64.b64encode(page_jpeg(document[page.number - 1])).decode()}
            for page in pages
        ] if include_pages else [{"number": page.number, "kind": page.kind} for page in pages]
    return {
        "route": route, "route_label": ROUTES[route], "reasons": reasons, "results": results,
        "fields": fields, "pages": images, "claimed_code": claimed_code, "form": form_label,
        "check_date": check_date.isoformat(), "rule_version": version["id"] if version else None,
        "mode": mode, "mode_label": MODE_LABELS[mode], "model": model,
        "requested_model": model_name() if mode == "live" else None, "model_usage": model_usage,
        "prompt_version": PROMPT_VERSION if mode == "live" else None, "schema_version": SCHEMA_VERSION,
    }
