"""Порівняння витягання з розміткою. У stdout виводяться лише агрегати."""

import argparse
from collections import defaultdict
import json
from pathlib import Path

from mono_check.analysis import analyze
from mono_check.pdf import FIELD_LABELS, normalize


ROOT = Path(__file__).resolve().parent.parent
DEMO_DATE = "2026-11-24"


def samples(private_manifest):
    if private_manifest:
        manifest = json.loads(private_manifest.read_text(encoding="utf-8"))
        for item in manifest:
            yield {**item, "path": private_manifest.parent / item["pdf"]}
        return
    examples = json.loads((ROOT / "output" / "examples.json").read_text(encoding="utf-8"))
    gold = json.loads((ROOT / "eval" / "synthetic.json").read_text(encoding="utf-8"))
    for sample in examples:
        yield {**sample, **gold[sample["id"]], "path": ROOT / "output" / "pdf" / (sample["pdf"] + ".pdf")}


def aggregate(items, mode):
    fields = {"text": defaultdict(lambda: [0, 0]), "scan": defaultdict(lambda: [0, 0])}
    states_ok = states_total = routes_ok = routes_total = false_pass = unverified_pass = 0
    documents = 0
    for item in items:
        documents += 1
        report = analyze(
            item["path"].read_bytes(), mode, item.get("claimed_code", ""), item.get("date", DEMO_DATE),
            include_pages=False, include_explanation=False,
        )
        for field, expected in item["fields"].items():
            kind = item.get("field_sources", {}).get(field, "text")
            if "field_sources" not in item and item.get("id") in {"scan", "mixed"}:
                kind = "text" if item["id"] == "mixed" and field == "issued_date" else "scan"
            options = report["fields"][field]["findings"]
            correct = len(options) == 1 and normalize(options[0]["value"]) == normalize(expected) and options[0]["source"] == kind
            score = fields[kind][field]
            score[0] += int(correct)
            score[1] += 1
        for field, expected in item.get("states", {}).items():
            states_total += 1
            states_ok += int(report["fields"][field]["state"] == expected)
        if "route" in item:
            routes_total += 1
            routes_ok += int(report["route"] == item["route"])
        for rule in report["results"]:
            if rule["status"] != "PASS":
                continue
            false_pass += int(rule["id"] in item.get("forbidden_pass", []))
            for proof in rule["evidence"]:
                expected = item["fields"].get(proof["field"])
                if expected is not None:
                    unverified_pass += int(normalize(expected) != normalize(proof["value"]))
    return {
        "mode": mode, "documents": documents, "fields": fields, "states": [states_ok, states_total],
        "routes": [routes_ok, routes_total], "false_pass": false_pass, "pass_with_wrong_value": unverified_pass,
    }


def render(report):
    print(f"Режим: {report['mode']}. Документів: {report['documents']}.")
    print("| Поле | Текст | Скан |")
    print("|---|---:|---:|")
    for key, label in FIELD_LABELS.items():
        scores = [report["fields"][kind].get(key) for kind in ("text", "scan")]
        values = [f"{value[0]}/{value[1]}" if value else "—" for value in scores]
        if any(scores):
            print(f"| {label} | {values[0]} | {values[1]} |")
    for label, key in (("Стани полів", "states"), ("Маршрути", "routes")):
        correct, total = report[key]
        if total:
            print(f"{label}: {correct}/{total}")
    print(f"Хибний PASS у негативних сценаріях: {report['false_pass']}. PASS із помилковим значенням: {report['pass_with_wrong_value']}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Локальна розмітка у форматі масиву JSON.")
    parser.add_argument("--mode", choices=("local", "replay", "live"))
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args()
    mode = options.mode or ("local" if options.manifest else "replay")
    report = aggregate(samples(options.manifest), mode)
    if options.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        render(report)
    if report["false_pass"] or report["pass_with_wrong_value"] or report["routes"][0] != report["routes"][1]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
