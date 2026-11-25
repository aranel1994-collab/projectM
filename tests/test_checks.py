"""Регресії маршрутів, доказів та поведінки моделі."""

from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import fitz

from eval.run import aggregate, samples
from mono_check.analysis import analyze
from mono_check.extraction import ExtractionError, SCHEMA, live_findings, local_findings, structured_response, verify
from mono_check.pdf import DocumentError, page_content, sections_from


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


class ChecksTest(unittest.TestCase):
    def test_all_demo_routes_and_expected_fields(self):
        result = aggregate(samples(None), MODE)
        self.assertEqual(result["routes"], [8, 8])
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
        with patch("mono_check.analysis.live_findings", return_value=raw), patch("mono_check.analysis.explain", return_value={}):
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
            with patch("mono_check.extraction.structured_response", side_effect=[{}, raw]) as provider:
                self.assertEqual(live_findings(document, pages), raw)
        self.assertEqual(provider.call_count, 2)
        supplied = provider.call_args.args[1]
        self.assertEqual(provider.call_args.args[3], SCHEMA)
        self.assertEqual(len([item for item in supplied if item["type"] == "input_image"]), 1)
        self.assertTrue(any(item.get("image_url", "").startswith("data:image/jpeg;base64,") for item in supplied))

    def test_responses_api_request_and_structured_output(self):
        response = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"code": "00073142"}'}]}],
        }
        fake_stream = BytesIO(json.dumps(response).encode())
        with patch("mono_check.extraction.api_key", return_value="test-key"), patch(
            "mono_check.extraction.urlopen", return_value=fake_stream
        ) as urlopen:
            result = structured_response("Read PDF", [{"type": "input_text", "text": "sample"}], "edr_fields", SCHEMA)
        self.assertEqual(result, {"code": "00073142"})
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
        with patch("mono_check.analysis.live_findings", return_value=raw), patch("mono_check.extraction.api_key", return_value="test"):
            with patch("mono_check.extraction.structured_response", side_effect=ExtractionError("timeout")):
                fallback = analyze(data, "live", check_date=DAY, include_pages=False)
            with patch("mono_check.extraction.structured_response", return_value={"DOC-02": "Потрібен доказ із розділу компанії."}):
                explained = analyze(data, "live", check_date=DAY, include_pages=False)
        self.assertEqual(fallback["route"], explained["route"])
        self.assertEqual(lookup(fallback, "DOC-02")["action"], lookup(explained, "DOC-02")["action"])
        self.assertIsNone(lookup(fallback, "DOC-02")["llm_note"])
        self.assertTrue(lookup(explained, "DOC-02")["llm_note"])


if __name__ == "__main__":
    unittest.main()
