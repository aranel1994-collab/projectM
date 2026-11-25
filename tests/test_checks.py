"""Регресії маршрутів, доказів та поведінки моделі."""

from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import fitz

from eval.run import aggregate, samples
from mono_check.analysis import analyze
from mono_check.extraction import ExtractionError, ModelResponse, SCHEMA, live_findings, local_findings, structured_response, verify
from mono_check.pdf import DocumentError, Page, page_content, sections_from


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "output" / "pdf"
MODE = "replay"
DAY = "2026-11-24"


def fixture(name):
    return (FIXTURES / (name + ".pdf")).read_bytes()


def run(name, claimed="", selected_date=DAY, mode=MODE):
    return analyze(fixture(name), mode, claimed, selected_date, include_pages=False)


def lookup(report, identifier):
    return next(item for item in report["results"] if item["id"] == identifier)


def on_text_page(lines, findings=None):
    pages = [Page(1, "\n".join(lines), "text")]
    with patch("mono_check.analysis.page_content", return_value=pages):
        if findings is None:
            return run("standard", mode="local")
        raw = local_findings(sections_from(pages)[2])
        raw.update({field: {"state": "present", "findings": [
            {"value": value, "page": 1, "fragment": fragment},
        ]} for field, (value, fragment) in findings.items()})
        with patch("mono_check.analysis.live_findings", return_value=ModelResponse(raw, "served-model")):
            return run("standard", mode="live")


class ChecksTest(unittest.TestCase):
    def test_all_demo_routes_and_expected_fields(self):
        result = aggregate(samples(None), MODE)
        self.assertEqual(result["routes"], [10, 10])
        self.assertEqual(result["false_pass"], 0)
        self.assertEqual(result["pass_with_wrong_value"], 0)
        for group in result["fields"].values():
            for key, pair in group.items():
                self.assertEqual(pair[0], pair[1], key)

    def test_standard_has_open_checks_and_highlight(self):
        result = run("standard", "00073142")
        self.assertEqual(result["route"], "STANDARD")
        self.assertEqual(lookup(result, "DOC-02")["status"], "PASS")
        self.assertEqual(lookup(result, "DOC-05")["status"], "NOT_APPLICABLE")
        self.assertEqual(lookup(result, "DOC-08")["status"], "PASS")
        self.assertTrue(lookup(result, "DOC-02")["evidence"][0]["positions"])
        for identifier in ("ADD-03", "EXT-01", "EXT-02", "EXT-03"):
            self.assertEqual(lookup(result, identifier)["status"], "NOT_CHECKED")

    def test_mismatch_does_not_fail_the_pdf_code(self):
        result = run("standard", "00170934")
        self.assertEqual(result["route"], "MANUAL_REVIEW")
        self.assertIn("CASE_ENTITY_MISMATCH", {x["code"] for x in result["reasons"]})
        self.assertEqual(lookup(result, "DOC-02")["status"], "PASS")
        self.assertEqual(lookup(result, "DOC-07")["status"], "REVIEW")

    def test_conflict_is_detected_even_if_model_returns_one_code(self):
        data = fixture("conflict")
        with fitz.open(stream=data, filetype="pdf") as document:
            pages = page_content(document)
            _, _, sections = sections_from(pages)
            raw = local_findings(sections)
            raw["code"]["findings"] = raw["code"]["findings"][:1]
        with patch("mono_check.analysis.live_findings", return_value=ModelResponse(raw)), patch("mono_check.analysis.explain", return_value={}):
            result = analyze(data, "live", check_date=DAY, include_pages=False)
        self.assertEqual(result["route"], "ESCALATE")
        self.assertEqual(len(lookup(result, "DOC-07")["evidence"]), 2)

    def test_evidence_must_match_context_and_page(self):
        data = fixture("founder")
        with fitz.open(stream=data, filetype="pdf") as document:
            pages = page_content(document)
            _, _, sections = sections_from(pages)
            raw = local_findings(sections)
            raw["code"] = {"state": "present", "findings": [{"value": "00170934", "page": 1, "fragment": "00170934"}]}
            raw["name"]["findings"][0]["page"] = 2
            fields, rejected = verify(document, pages, sections, raw)
        self.assertEqual(fields["code"]["state"], "ungrounded")
        self.assertEqual(fields["name"]["state"], "ungrounded")
        self.assertIn("code", rejected)

    def test_terminated_company_and_termination_section_block_standard_route(self):
        for key, route, reason, status in (
            ("terminated", "ESCALATE", "ENTITY_TERMINATED", "FAIL"),
            ("terminating", "MANUAL_REVIEW", "ENTITY_STATUS_REVIEW", "REVIEW"),
        ):
            with self.subTest(scenario=key):
                result = run(key, mode="local")
                self.assertEqual(result["route"], route)
                self.assertIn(reason, {row["code"] for row in result["reasons"]})
                self.assertEqual(lookup(result, "DOC-08")["status"], status)
                self.assertTrue(lookup(result, "DOC-08")["evidence"])

    def test_live_preserves_termination_evidence_when_model_omits_it(self):
        data = fixture("terminated")
        with fitz.open(stream=data, filetype="pdf") as doc:
            raw = local_findings(sections_from(page_content(doc))[2])
        raw["company_status"] = {"state": "section_absent", "findings": []}
        with patch("mono_check.analysis.live_findings", return_value=ModelResponse(raw, "served-model")):
            result = run("terminated", mode="live")
        self.assertEqual(result["route"], "ESCALATE")
        self.assertEqual(result["model"], "served-model")

    def test_termination_not_reported_and_unconfirmed_status(self):
        base = [
            "ВИПИСКА",
            "ТОВ «Демо»",
            "Ідентифікаційний код юридичної особи: 00073142",
            "Місцезнаходження юридичної особи: м. Київ",
            "Дата та номер запису: 12.05.2025, 1000000000000123456",
            "Керівник юридичної особи: Прикладенко Марта",
        ]
        no_termination = "Дані про перебування юридичної особи в процесі припинення: Відомості відсутні"
        confirmed = on_text_page([*base, "Стан юридичної особи: зареєстровано", no_termination])
        self.assertEqual(confirmed["route"], "STANDARD")
        for status in ("", "в стані припинення", "банкрутство", "скасовано", "невідомо"):
            with self.subTest(status=status):
                lines = [*base, f"Стан юридичної особи: {status}", no_termination]
                result = on_text_page(lines)
                self.assertEqual(result["route"], "MANUAL_REVIEW")
                self.assertEqual(lookup(result, "DOC-08")["status"], "REVIEW")

    def test_model_quotes_on_unfamiliar_template_stay_visible_and_manual(self):
        name = "ТОВ «Незнайомий шаблон»"
        code, address, registered, number, manager = "00073142", "м. Київ", "12.05.2025", "1000000000000123456", "Прикладенко Марта"
        evidence = {
            "name": (name, f"Найменування: {name}"),
            "code": (code, f"ЄДРПОУ: {code}"),
            "address": (address, f"Адреса юридичної особи: {address}"),
            "registration_date": (registered, f"Дата державної реєстрації: {registered}"),
            "registration_number": (number, f"№ реєстраційного запису: {number}"),
            "management": (manager, f"Керівник: {manager}"),
        }
        lines = [
            "ВИПИСКА", "Найменування:", name,
            *(quote for field, (_, quote) in evidence.items() if field != "name"),
            "Стан юридичної особи: зареєстровано",
        ]
        result = on_text_page(lines, evidence)
        self.assertEqual(result["route"], "MANUAL_REVIEW")
        self.assertEqual({row["code"] for row in result["reasons"]}, {"MODEL_ONLY_EVIDENCE"})
        for field, (value, _) in evidence.items():
            with self.subTest(field=field):
                self.assertEqual(result["fields"][field]["state"], "text_model")
                self.assertEqual(result["fields"][field]["findings"][0]["value"], value)
        for rule in ("DOC-01", "DOC-02", "DOC-03", "DOC-04"):
            self.assertEqual(lookup(result, rule)["status"], "REVIEW")
        wrong = on_text_page(lines, {**evidence, "address": ("м. Одеса", "Адреса юридичної особи: м. Одеса")})
        self.assertEqual(wrong["fields"]["address"]["state"], "ungrounded")
        self.assertIn("UNGROUNDED_VALUES", {row["code"] for row in wrong["reasons"]})

    def test_model_fallback_rejects_other_people_codes(self):
        for heading in ("Дані про засновника:", "Кінцевий бенефіціарний власник:", "Державний реєстратор:"):
            with self.subTest(heading=heading):
                quoted = "ЄДРПОУ: 00170934"
                result = on_text_page(["ВИПИСКА", heading, quoted], {"code": ("00170934", quoted)})
                self.assertEqual(result["fields"]["code"]["state"], "ungrounded")
                self.assertEqual(result["fields"]["code"]["findings"], [])

    def test_dates_with_time_are_normalized_before_evidence_check(self):
        quote = "30.06.2025 11:20:00"
        result = on_text_page(["ВИПИСКА", f"Дата формування виписки: {quote}"], {"issued_date": (quote, quote)})
        self.assertEqual(result["fields"]["issued_date"]["state"], "present")
        self.assertEqual(result["fields"]["issued_date"]["findings"][0]["value"], "30.06.2025")
        self.assertEqual(lookup(result, "DOC-06")["status"], "NOT_CHECKED")
        self.assertIn("дн.", lookup(result, "DOC-06")["summary"])

    def test_future_registration_and_incomplete_shares_and_missing_manager(self):
        result = on_text_page([
            "ВИПИСКА", "ТОВ «Демо»", "Ідентифікаційний код юридичної особи: 00073142",
            "Місцезнаходження юридичної особи: м. Київ",
            "Дата та номер запису: 01.01.2030, 1000000000000123456",
            "Керівник юридичної особи: Інформація відсутня",
            "Стан юридичної особи: зареєстровано",
            "Відомості про засновників: Особа А 30%, Особа Б 20%",
            "Кінцевий бенефіціарний власник: Особа А",
        ])
        self.assertEqual(result["route"], "MANUAL_REVIEW")
        self.assertEqual(result["fields"]["management"]["state"], "explicit_absent")
        self.assertEqual(lookup(result, "DOC-03")["status"], "REVIEW")
        self.assertEqual(lookup(result, "DOC-05")["status"], "REVIEW")

    def test_ownership_states(self):
        self.assertEqual(run("standard")["fields"]["ubo"]["state"], "section_absent")
        missing = run("missing-ubo")
        self.assertEqual(missing["fields"]["ubo"]["state"], "explicit_absent")
        self.assertEqual(lookup(missing, "DOC-05")["status"], "REVIEW")
        self.assertEqual(run("founder")["fields"]["code"]["state"], "empty")

    def test_scan_and_mixed_remain_manual(self):
        for name in ("scan", "mixed"):
            report = run(name)
            with self.subTest(name=name):
                self.assertEqual(report["fields"]["code"]["state"], "scan")
                self.assertEqual(report["route"], "MANUAL_REVIEW")
                self.assertIn("SCAN_SOURCE", {row["code"] for row in report["reasons"]})
                self.assertNotEqual(lookup(report, "DOC-02")["status"], "PASS")
        self.assertEqual(run("mixed")["fields"]["issued_date"]["state"], "present")
        self.assertEqual(run("mixed", mode="local")["fields"]["code"]["state"], "unreadable")

    def test_document_note_cannot_set_route(self):
        original = run("standard", "00170934")
        injected = run("document-note", "00170934")
        self.assertEqual(original["route"], injected["route"])
        self.assertEqual(original["reasons"], injected["reasons"])

    def test_version_selection_and_bad_inputs(self):
        result = run("standard", selected_date="2026-10-22")
        self.assertIsNone(result["rule_version"])
        self.assertEqual(result["route"], "MANUAL_REVIEW")
        self.assertIn("RULE_VERSION_UNAVAILABLE", {row["code"] for row in result["reasons"]})
        with self.assertRaises(DocumentError):
            analyze(b"fake", check_date=DAY)
        with self.assertRaises(DocumentError):
            run("standard", "123")
        with self.assertRaises(ExtractionError):
            data = fixture("standard").replace(b"%%EOF", b"\n%%EOF")
            analyze(data, "replay", check_date=DAY)

    def test_multimodal_payload_and_one_retry_for_schema(self):
        data = fixture("mixed")
        with fitz.open(stream=data, filetype="pdf") as document:
            pages = page_content(document)
            raw = local_findings(sections_from(pages)[2])
            with patch("mono_check.extraction.structured_response", side_effect=[ModelResponse({}), ModelResponse(raw)]) as provider:
                self.assertEqual(live_findings(document, pages).data, raw)
        self.assertEqual(provider.call_count, 2)
        supplied = provider.call_args.args[1]
        self.assertEqual(provider.call_args.args[3], SCHEMA)
        self.assertEqual(len([item for item in supplied if item["type"] == "input_image"]), 1)
        self.assertTrue(any(item.get("image_url", "").startswith("data:image/jpeg;base64,") for item in supplied))

    def test_responses_api_request_and_structured_output(self):
        response = {
            "status": "completed",
            "model": "gpt-6.2-sol-test-snapshot",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"code": "00073142"}'}]}],
        }
        fake_stream = BytesIO(json.dumps(response).encode())
        with patch("mono_check.extraction.api_key", return_value="test-key"), patch(
            "mono_check.extraction.urlopen", return_value=fake_stream
        ) as urlopen:
            result = structured_response("Read PDF", [{"type": "input_text", "text": "sample"}], "edr_fields", SCHEMA)
        self.assertEqual(result.data, {"code": "00073142"})
        self.assertEqual(result.model, response["model"])
        self.assertEqual(result.usage, response["usage"])
        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["text"]["format"]["schema"], SCHEMA)

    def test_optional_explanation_falls_back_without_route_changes(self):
        data = fixture("founder")
        with fitz.open(stream=data, filetype="pdf") as document:
            raw = local_findings(sections_from(page_content(document))[2])
        with patch("mono_check.analysis.live_findings", return_value=ModelResponse(raw)), patch("mono_check.extraction.api_key", return_value="test"):
            with patch("mono_check.extraction.structured_response", side_effect=ExtractionError("timeout")):
                fallback = analyze(data, "live", check_date=DAY, include_pages=False)
            with patch("mono_check.extraction.structured_response", return_value=ModelResponse({"DOC-02": "Потрібен доказ із розділу компанії."})):
                explained = analyze(data, "live", check_date=DAY, include_pages=False)
        self.assertEqual(fallback["route"], explained["route"])
        self.assertEqual(lookup(fallback, "DOC-02")["action"], lookup(explained, "DOC-02")["action"])
        self.assertIsNone(lookup(fallback, "DOC-02")["llm_note"])
        self.assertTrue(lookup(explained, "DOC-02")["llm_note"])


if __name__ == "__main__":
    unittest.main()
