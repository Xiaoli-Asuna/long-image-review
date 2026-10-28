import _bootstrap
import base64
import copy
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from provider import ImageInput, ProviderConfig, ProviderError, VisionJsonProvider, validate_findings


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=")
REPORT = {
    "schema_version": "review.v1",
    "findings": [{"id": "F1", "status": "needs_confirmation", "image_id": "tile-1", "box": [0.1, 0.2, 0.5, 0.3],
                  "rule_id": "R1", "rule_quote": "深色背景使用反白 LOGO"}],
    "coverage": {"logo": {"status": "partial", "reason": "待人工核对"},
                 "font_file_identity": {"status": "not_checked", "reason": "仅有位图"}},
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        time.sleep(self.server.delay)
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        if self.server.status == 302:
            self.send_header("Location", "https://example.invalid/redirect")
        self.end_headers()
        try:
            self.wfile.write(self.server.response)
        except OSError:
            pass


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls = []
        self.server.delay = 0
        self.server.status = 200
        self.reply()

    def reply(self, value=REPORT, *, reason="stop", usage=True):
        envelope = {"id": "fixture-response", "choices": [{"finish_reason": reason, "message": {"content": json.dumps(value, ensure_ascii=False)}}]}
        if usage:
            envelope["usage"] = {"prompt_tokens": 1234, "completion_tokens": 56}
        self.server.response = json.dumps(envelope).encode()

    def provider(self, **kwargs):
        config = ProviderConfig("local", f"http://127.0.0.1:{self.server.server_port}/v1", "fixture-vision", **kwargs)
        return VisionJsonProvider(config)

    def call(self, provider=None, **kwargs):
        return (provider or self.provider()).generate_json(task="fixture review", payload={"copy": "测试"},
                     images=[ImageInput("tile-1", "image/png", PNG)], schema={"type": "object"}, **kwargs)

    def test_actual_loopback_vision_request_and_provenance(self):
        result = self.call(client_request_id="job-001")
        self.assertEqual(result.value, REPORT)
        self.assertEqual(result.usage["prompt_tokens"], 1234)
        self.assertEqual(result.client_request_id, "job-001")
        self.assertEqual(result.response_id, "fixture-response")
        sent = self.server.calls[0]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["body"]["model"], "fixture-vision")
        image = sent["body"]["messages"][1]["content"][-1]["image_url"]["url"]
        self.assertEqual(base64.b64decode(image.split(",")[1]), PNG)
        self.assertNotIn("Authorization", sent["headers"])

    def test_cloud_configuration_uses_same_contract_without_network(self):
        config = ProviderConfig("cloud", "https://example.invalid/v1", "qwen3-vl-plus", "offline-test-key", disable_thinking=True)
        provider = VisionJsonProvider(config)
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, limit): return ProviderTests.server.response
        with patch.object(provider._opener, "open", return_value=Reply()) as mocked:
            result = self.call(provider)
        req = mocked.call_args.args[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer offline-test-key")
        self.assertTrue(json.loads(req.data)["enable_thinking"] is False)
        self.assertEqual(result.value, REPORT)
        self.assertNotIn("offline-test-key", repr(config))
        self.assertEqual(len(self.server.calls), 0)

    def test_local_model_and_json_mode_can_be_changed(self):
        config = ProviderConfig.from_env({"REVIEW_PROVIDER": "local", "REVIEW_API_BASE": f"http://127.0.0.1:{self.server.server_port}/v1", "REVIEW_MODEL": "Qwen/Qwen3-VL-8B-Instruct", "REVIEW_JSON_MODE": "false"})
        result = self.call(VisionJsonProvider(config))
        self.assertEqual(result.model, "Qwen/Qwen3-VL-8B-Instruct")
        self.assertNotIn("response_format", self.server.calls[0]["body"])
        self.assertNotIn("enable_thinking", self.server.calls[0]["body"])

    def test_cloud_without_key_is_rejected_before_network(self):
        with self.assertRaises(ValueError): ProviderConfig.from_env({})
        self.assertEqual(self.server.calls, [])

    def test_secret_in_url_is_rejected(self):
        for url in ("https://key:secret@example.invalid/v1", "https://example.invalid/v1?key=secret"):
            with self.assertRaises(ValueError): ProviderConfig("cloud", url, "vision", "key")

    def test_http_error_is_sanitized_and_not_retried(self):
        self.server.status = 429
        self.server.response = b"private prompt offline-test-key"
        with self.assertRaises(ProviderError) as caught: self.call()
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn("offline-test-key", str(caught.exception))
        self.assertEqual(len(self.server.calls), 1)

    def test_redirect_is_not_followed(self):
        self.server.status = 302
        with self.assertRaises(ProviderError): self.call()
        self.assertEqual(len(self.server.calls), 1)

    def test_timeout_remains_unknown_and_is_not_retried(self):
        self.server.delay = 0.15
        with self.assertRaises(ProviderError) as caught: self.call(self.provider(timeout_seconds=0.02))
        self.assertEqual(caught.exception.outcome, "unknown")
        self.assertEqual(len(self.server.calls), 1)
        time.sleep(0.16)

    def test_truncated_model_output_does_not_become_report(self):
        self.reply(reason="length")
        with self.assertRaises(ProviderError) as caught: self.call()
        self.assertEqual(caught.exception.outcome, "incomplete")
        self.assertEqual(caught.exception.usage["completion_tokens"], 56)
        self.assertEqual(len(self.server.calls), 1)

    def test_invalid_json_does_not_become_report(self):
        self.server.response = b'{"choices": []}'
        with self.assertRaises(ProviderError) as caught: self.call()
        self.assertEqual(caught.exception.outcome, "invalid_response")

    def test_unknown_usage_is_none_not_zero(self):
        self.reply(usage=False)
        self.assertIsNone(self.call().usage)

    def test_invalid_image_and_duplicate_image_ids_fail_before_network(self):
        provider = self.provider()
        with self.assertRaises(ValueError): ImageInput("bad", "image/png", b"not-an-image").as_content()
        with self.assertRaises(ValueError): provider.generate_json(task="x", payload={}, schema={}, images=[ImageInput("same", "image/png", PNG)] * 2)
        self.assertEqual(self.server.calls, [])

    def test_request_id_cannot_inject_headers(self):
        with self.assertRaises(ValueError): self.call(client_request_id="id\r\nHeader: bad")
        self.assertEqual(self.server.calls, [])


class ContractTests(unittest.TestCase):
    def check(self, report, **kwargs):
        return validate_findings(report, image_ids={"tile-1"}, rules={"R1": "品牌要求：深色背景使用反白 LOGO。"}, **kwargs)

    def test_valid_evidence(self): self.assertEqual(len(self.check(REPORT)), 1)

    def test_unknown_image_rule_and_invented_quote_are_rejected(self):
        for key, value in (("image_id", "invented"), ("rule_id", "invented"), ("rule_quote", "随意拉伸 LOGO")):
            report = copy.deepcopy(REPORT)
            report["findings"][0][key] = value
            with self.assertRaises(ValueError): self.check(report)

    def test_invalid_boxes_are_rejected(self):
        for box in ([0, 0, 1.1, 1], [0.5, 0, 0.1, 1], [0, 0, float("nan"), 1], [False, 0, 1, 1]):
            report = copy.deepcopy(REPORT)
            report["findings"][0]["box"] = box
            with self.assertRaises(ValueError): self.check(report)

    def test_model_cannot_issue_human_approval(self):
        report = copy.deepcopy(REPORT)
        report["findings"][0]["status"] = "approved"
        with self.assertRaises(ValueError): self.check(report)

    def test_raster_cannot_certify_font_file(self):
        report = copy.deepcopy(REPORT)
        report["coverage"]["font_file_identity"]["status"] = "checked"
        with self.assertRaises(ValueError): self.check(report)

    def test_missing_coverage_is_rejected_even_without_findings(self):
        report = {"schema_version": "review.v1", "findings": [], "coverage": {}}
        with self.assertRaises(ValueError): self.check(report)
        with self.assertRaises(ValueError): self.check(REPORT, required_checks={"logo", "copy"})


if __name__ == "__main__":
    unittest.main()
