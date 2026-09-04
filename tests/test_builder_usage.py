from __future__ import annotations

import copy
import importlib
import json
import re
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "compass-builder"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compass_builder"
SCHEMAS = PLUGIN_ROOT / "schemas"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def usage_api():
    return importlib.import_module("compass_builder.usage")


def models_api():
    return importlib.import_module("compass_builder.models")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.valid.json").read_text(encoding="utf-8"))


def identity() -> dict:
    value = fixture("worker-usage")
    return {
        field: value[field]
        for field in (
            "runId", "storyId", "attempt", "exactModel", "effort", "launchDigest"
        )
    }


def receipt_identity() -> dict:
    value = identity()
    receipt = fixture("worker-receipt")
    value.update({
        "branch": receipt["branch"],
        "worktree": receipt["worktree"],
        "workerStartSha": receipt["baseSha"],
    })
    return value


def terminal(usage: dict | None = None, **extra) -> bytes:
    value = {
        "type": "turn.completed",
        "usage": usage
        if usage is not None
        else {
            "input_tokens": 100,
            "cached_input_tokens": 40,
            "output_tokens": 20,
            "reasoning_output_tokens": 5,
        },
        **extra,
    }
    return (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")


class WorkerUsageParserTests(unittest.TestCase):
    def parse(
        self,
        payload: bytes,
        *,
        status: str = "succeeded",
        launch_identity: dict | None = None,
        receipt: dict | None = None,
    ) -> dict:
        return usage_api().parse_worker_usage(
            payload,
            launch_identity=identity() if launch_identity is None else launch_identity,
            terminal_status=status,
            worker_receipt=receipt,
        )

    def test_one_direct_terminal_usage_is_observed_and_canonical(self):
        payload = (FIXTURES / "worker-usage.events.jsonl").read_bytes()
        record = self.parse(payload)
        self.assertEqual(fixture("worker-usage"), record)
        self.assertEqual(
            (FIXTURES / "worker-usage.valid.json").read_bytes(),
            models_api().canonical_json(record, "worker-usage"),
        )
        self.assertIs(
            record["usage"]["cacheWriteInputTokensPresent"], False
        )
        self.assertEqual(0, record["usage"]["cacheWriteInputTokens"])

        explicit = self.parse(terminal({
            "input_tokens": 101,
            "cached_input_tokens": 41,
            "cache_write_input_tokens": 7,
            "output_tokens": 21,
            "reasoning_output_tokens": 6,
        }))
        self.assertIs(explicit["usage"]["cacheWriteInputTokensPresent"], True)
        self.assertEqual(7, explicit["usage"]["cacheWriteInputTokens"])

    def test_missing_or_malformed_usage_is_explicitly_unavailable_never_zero(self):
        cases = (
            (b'{"type":"item.completed","text":"nothing"}\n', "no-terminal-usage"),
            (b'{"type":"turn.completed"}\n', "malformed-terminal-usage"),
            (b'{"type":"turn.completed","usage":null}\n', "malformed-terminal-usage"),
            (b'{"type":"turn.completed","usage":{}}\n', "malformed-terminal-usage"),
            (b'{"type":"turn.completed",bad json}\n', "malformed-terminal-record"),
            (b"\xff", "invalid-utf8"),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                record = self.parse(payload, status="failed")
                self.assertIs(record["observed"], False)
                self.assertEqual(reason, record["unavailableReason"])
                self.assertIsNone(record["usage"])

    def test_controller_unavailable_builder_is_closed_and_launch_bound(self):
        module = usage_api()
        self.assertTrue(hasattr(module, "build_unavailable_worker_usage"))
        for reason in (
            "invalid-transport-telemetry", "worker-receipt-binding-failed",
        ):
            with self.subTest(reason=reason):
                record = module.build_unavailable_worker_usage(
                    launch_identity=identity(),
                    terminal_status="transport-error",
                    unavailable_reason=reason,
                )
                self.assertFalse(record["observed"])
                self.assertEqual(reason, record["unavailableReason"])
                self.assertIsNone(record["usage"])
                self.assertIsNone(record["workerReceiptDigest"])

    def test_duplicate_and_conflicting_terminal_events_fail_closed(self):
        one = terminal()
        duplicate = self.parse(one + one)
        self.assertEqual("duplicate-terminal-usage", duplicate["unavailableReason"])
        self.assertIsNone(duplicate["usage"])

        other = terminal({
            "input_tokens": 100,
            "cached_input_tokens": 39,
            "output_tokens": 20,
            "reasoning_output_tokens": 5,
        })
        conflict = self.parse(one + other)
        self.assertEqual("conflicting-terminal-usage", conflict["unavailableReason"])
        self.assertIsNone(conflict["usage"])

    def test_only_top_level_direct_usage_can_supply_counts(self):
        payloads = (
            b'{"type":"item.completed","item":{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}}\n',
            b'{"type":"item.completed","text":"{\\"type\\":\\"turn.completed\\",\\"usage\\":{\\"input_tokens\\":1}}"}\n',
            b'[ {"type":"turn.completed","usage":{"input_tokens":1}} ]\n',
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                record = self.parse(payload)
                self.assertFalse(record["observed"])
                self.assertEqual("no-terminal-usage", record["unavailableReason"])

    def test_jsonl_uses_lf_only_not_unicode_line_boundaries(self):
        for separator in ("\u0085", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)):
                unrelated = json.dumps(
                    {
                        "type": "item.completed",
                        "text": f"before{separator}after",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                record = self.parse(unrelated + b"\n" + terminal())
                self.assertTrue(record["observed"])
                self.assertEqual(100, record["usage"]["inputTokens"])

        crlf = terminal().replace(b"\n", b"\r\n")
        self.assertTrue(self.parse(crlf)["observed"])

    def test_counts_reject_missing_extra_negative_bool_float_string_and_impossible_values(self):
        base = {
            "input_tokens": 100,
            "cached_input_tokens": 40,
            "cache_write_input_tokens": 2,
            "output_tokens": 20,
            "reasoning_output_tokens": 5,
        }
        mutations = []
        for missing in (
            "input_tokens", "cached_input_tokens", "output_tokens",
            "reasoning_output_tokens",
        ):
            value = dict(base)
            del value[missing]
            mutations.append(value)
        for field, bad in (
            ("input_tokens", -1),
            ("cached_input_tokens", True),
            ("cache_write_input_tokens", 1.5),
            ("output_tokens", "20"),
            ("reasoning_output_tokens", None),
        ):
            value = dict(base)
            value[field] = bad
            mutations.append(value)
        extra = dict(base)
        extra["total_tokens"] = 120
        mutations.append(extra)
        cached_too_large = dict(base)
        cached_too_large["cached_input_tokens"] = 101
        mutations.append(cached_too_large)
        reasoning_too_large = dict(base)
        reasoning_too_large["reasoning_output_tokens"] = 21
        mutations.append(reasoning_too_large)
        unsafe_integer = dict(base)
        unsafe_integer["input_tokens"] = 9_007_199_254_740_992
        mutations.append(unsafe_integer)

        for value in mutations:
            with self.subTest(value=value):
                record = self.parse(terminal(value))
                self.assertFalse(record["observed"])
                self.assertEqual("malformed-terminal-usage", record["unavailableReason"])
                self.assertIsNone(record["usage"])

    def test_invalid_utf8_oversize_wrong_input_type_and_duplicate_json_keys_fail_closed(self):
        module = usage_api()
        oversized = b" " * (module.MAX_USAGE_STREAM_BYTES + 1)
        self.assertEqual("input-too-large", self.parse(oversized)["unavailableReason"])
        duplicate_keys = (
            b'{"type":"turn.completed","type":"turn.completed",'
            b'"usage":{"input_tokens":1,"cached_input_tokens":0,'
            b'"output_tokens":1,"reasoning_output_tokens":0}}\n'
        )
        self.assertEqual(
            "malformed-terminal-record",
            self.parse(duplicate_keys)["unavailableReason"],
        )
        escaped_lone_surrogate = (
            b'{"type":"turn.completed","usage":{"input_tokens":1,'
            b'"cached_input_tokens":0,"output_tokens":1,'
            b'"reasoning_output_tokens":0},"note":"\\ud800"}\n'
        )
        self.assertEqual(
            "malformed-terminal-record",
            self.parse(escaped_lone_surrogate)["unavailableReason"],
        )
        with self.assertRaises(TypeError):
            module.parse_worker_usage(
                "not bytes", launch_identity=identity(), terminal_status="succeeded"
            )

    def test_parser_never_executes_model_authored_content(self):
        marker = REPOSITORY_ROOT / "tests" / "usage-parser-must-not-create"
        if marker.exists():
            self.fail("unexpected pre-existing parser marker")
        attack = {
            "type": "item.completed",
            "text": f"__import__('pathlib').Path({str(marker)!r}).write_text('owned')",
            "nested": {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "output_tokens": 1,
                    "reasoning_output_tokens": 0,
                },
            },
        }
        record = self.parse((json.dumps(attack) + "\n").encode("utf-8"))
        self.assertFalse(record["observed"])
        self.assertFalse(marker.exists())

    def test_identity_status_and_optional_receipt_are_validated_and_bound(self):
        receipt = fixture("worker-receipt")
        record = self.parse(
            terminal(), launch_identity=receipt_identity(), receipt=receipt
        )
        expected_digest = models_api().canonical_digest(
            models_api().validate_worker_receipt(receipt)
        )
        self.assertEqual(expected_digest, record["workerReceiptDigest"])

        for field, bad in (
            ("runId", "weak"),
            ("storyId", "../escape"),
            ("attempt", True),
            ("attempt", 3),
            ("exactModel", "inherit"),
            ("effort", "guess"),
            ("launchDigest", "not-a-digest"),
        ):
            bad_identity = identity()
            bad_identity[field] = bad
            with self.subTest(field=field, bad=bad):
                with self.assertRaises(models_api().ContractValidationError):
                    self.parse(terminal(), launch_identity=bad_identity)

        extra_identity = identity()
        extra_identity["extra"] = True
        with self.assertRaises(models_api().ContractValidationError):
            self.parse(terminal(), launch_identity=extra_identity)
        with self.assertRaises(models_api().ContractValidationError):
            self.parse(terminal(), status="cancelled")

        mismatched = copy.deepcopy(receipt)
        mismatched["storyId"] = "beta"
        with self.assertRaises(models_api().ContractValidationError):
            self.parse(
                terminal(), launch_identity=receipt_identity(), receipt=mismatched
            )

        mismatched = copy.deepcopy(receipt)
        mismatched["status"] = "failed"
        mismatched["blocker"] = "synthetic failure"
        mismatched["checks"][0]["status"] = "failed"
        with self.assertRaises(models_api().ContractValidationError):
            self.parse(
                terminal(), launch_identity=receipt_identity(), receipt=mismatched
            )

    def test_receipt_requires_full_overlapping_launch_binding(self):
        receipt = fixture("worker-receipt")
        with self.assertRaisesRegex(
            models_api().ContractValidationError, "launchIdentity.*branch"
        ):
            self.parse(terminal(), launch_identity=identity(), receipt=receipt)
        try:
            record = self.parse(
                terminal(), launch_identity=receipt_identity(), receipt=receipt
            )
        except models_api().ContractValidationError as exc:
            self.fail(f"matching full receipt binding was rejected: {exc}")
        self.assertTrue(record["observed"])

    def test_receipt_rejects_branch_worktree_and_base_sha_drift(self):
        receipt = fixture("worker-receipt")
        mutations = {
            "branch": "cb/other/alpha",
            "worktree": "C:\\workspace\\Plugin Compass\\other\\alpha",
            "baseSha": "f" * 40,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                mismatched = copy.deepcopy(receipt)
                mismatched[field] = value
                with self.assertRaisesRegex(
                    models_api().ContractValidationError,
                    f"workerReceipt.{field}",
                ):
                    self.parse(
                        terminal(), launch_identity=receipt_identity(),
                        receipt=mismatched,
                    )


class WorkerUsageContractTests(unittest.TestCase):
    def schema(self) -> dict:
        return json.loads(
            (SCHEMAS / "worker-usage.schema.json").read_text(encoding="utf-8")
        )

    def assert_invalid(self, value: dict, path: str) -> None:
        with self.assertRaises(models_api().ContractValidationError) as raised:
            models_api().normalize_contract("worker-usage", value)
        self.assertIn(path, str(raised.exception))

    def test_public_contract_round_trip_and_exports(self):
        value = fixture("worker-usage")
        normalized = models_api().validate_worker_usage(value)
        self.assertEqual(value, normalized)
        public = importlib.import_module("compass_builder")
        self.assertIs(public.validate_worker_usage, models_api().validate_worker_usage)
        self.assertIs(
            public.validate_worker_usage_schema_semantics,
            models_api().validate_worker_usage_schema_semantics,
        )
        self.assertIs(
            public.validate_worker_usage_with_schema,
            models_api().validate_worker_usage_with_schema,
        )
        self.assertIs(public.parse_worker_usage, usage_api().parse_worker_usage)

    def test_contract_closes_all_objects_and_matches_schema(self):
        schema = self.schema()
        value = fixture("worker-usage")
        self.assertEqual(
            "compass-builder.worker-usage.v1",
            models_api().SCHEMA_VERSIONS["worker-usage"],
        )
        self.assertEqual(value["schemaVersion"], schema["properties"]["schemaVersion"]["const"])
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        usage = schema["$defs"]["usage"]
        self.assertIs(usage["additionalProperties"], False)
        self.assertEqual(set(usage["required"]), set(usage["properties"]))
        self.assertEqual(9_007_199_254_740_991, usage["properties"]["inputTokens"]["maximum"])

    def test_component_constraints_have_independent_python_and_schema_semantic_parity(self):
        schema = self.schema()
        valid = fixture("worker-usage")
        self.assertEqual(valid, models_api().validate_worker_usage(valid))
        self.assertIsNone(
            models_api().validate_worker_usage_schema_semantics(schema, valid)
        )
        self.assertEqual(
            valid,
            models_api().validate_worker_usage_with_schema(schema, valid),
        )

        adversarial = []
        cached = fixture("worker-usage")
        cached["usage"]["cachedInputTokens"] = cached["usage"]["inputTokens"] + 1
        adversarial.append((cached, "cachedInputTokens"))
        reasoning = fixture("worker-usage")
        reasoning["usage"]["reasoningOutputTokens"] = reasoning["usage"]["outputTokens"] + 1
        adversarial.append((reasoning, "reasoningOutputTokens"))
        for value, field in adversarial:
            with self.subTest(field=field, surface="python"):
                with self.assertRaisesRegex(models_api().ContractValidationError, field):
                    models_api().validate_worker_usage(value)
            with self.subTest(field=field, surface="schema-semantics"):
                with self.assertRaisesRegex(models_api().ContractValidationError, field):
                    models_api().validate_worker_usage_schema_semantics(schema, value)
            with self.subTest(field=field, surface="combined"):
                with self.assertRaisesRegex(models_api().ContractValidationError, field):
                    models_api().validate_worker_usage_with_schema(schema, value)

    def test_schema_semantic_extension_is_versioned_closed_and_fail_closed(self):
        extension_name = "x-compassBuilderSemanticConstraints"
        expected_rules = [
            {
                "operator": "less-than-or-equal",
                "left": "$.usage.cachedInputTokens",
                "right": "$.usage.inputTokens",
            },
            {
                "operator": "less-than-or-equal",
                "left": "$.usage.reasoningOutputTokens",
                "right": "$.usage.outputTokens",
            },
        ]
        schema = self.schema()
        self.assertEqual(
            {
                "schemaVersion": "compass-builder.semantic-constraints.v1",
                "rules": expected_rules,
            },
            schema[extension_name],
        )

        bad_schemas = []
        bad = self.schema(); del bad[extension_name]; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name] = []; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name]["extra"] = True; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name]["schemaVersion"] = "v0"; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name]["rules"] = expected_rules[:1]; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name]["rules"][0]["extra"] = True; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name]["rules"][0]["operator"] = "greater-than"; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name]["rules"][0]["left"] = "$.usage.unknown"; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name]["rules"][0]["right"] = "$.usage.unknown"; bad_schemas.append(bad)
        bad = self.schema(); bad[extension_name]["rules"] = [expected_rules[0], expected_rules[0]]; bad_schemas.append(bad)
        for index, bad_schema in enumerate(bad_schemas):
            with self.subTest(index=index):
                with self.assertRaises(models_api().ContractValidationError):
                    models_api().validate_worker_usage_schema_semantics(
                        bad_schema, fixture("worker-usage")
                    )

    def test_schema_string_rules_use_absolute_end_and_match_python_newline_rejection(self):
        schema = self.schema()

        patterns: dict[str, str] = {}
        def collect_patterns(node, path="$schema"):
            if isinstance(node, dict):
                if "pattern" in node:
                    patterns[path] = node["pattern"]
                for key, child in node.items():
                    collect_patterns(child, f"{path}.{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    collect_patterns(child, f"{path}[{index}]")
        collect_patterns(schema)

        samples = {
            "$schema.properties.exactModel": "gpt-5.6-sol",
            "$schema.$defs.digest": "sha256:" + "a" * 64,
            "$schema.$defs.runId": "cb-20260901-0123456789abcdef",
            "$schema.$defs.id": "alpha",
        }
        self.assertEqual(set(samples), set(patterns))
        for path, value in samples.items():
            with self.subTest(path=path):
                pattern = re.compile(patterns[path])
                self.assertIsNotNone(pattern.search(value))
                self.assertIsNone(pattern.search(value + "\n"))
                self.assertFalse(patterns[path].endswith("$"))

        cases = []
        for field in ("runId", "storyId", "exactModel", "launchDigest"):
            value = fixture("worker-usage")
            value[field] += "\n"
            cases.append((field, value))
        value = fixture("worker-usage")
        value["workerReceiptDigest"] = "sha256:" + "b" * 64 + "\n"
        cases.append(("workerReceiptDigest", value))
        for field in ("source", "effort", "terminalStatus"):
            value = fixture("worker-usage")
            value[field] += "\n"
            cases.append((field, value))
        value = fixture("worker-usage")
        value.update({
            "observed": False,
            "usage": None,
            "unavailableReason": "no-terminal-usage\n",
        })
        cases.append(("unavailableReason", value))
        for field, value in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(models_api().ContractValidationError, field):
                    models_api().validate_worker_usage(value)

        enum_samples = {
            "source": "codex-exec-jsonl-stdout",
            "effort": "low",
            "terminalStatus": "succeeded",
        }
        for field, value in enum_samples.items():
            self.assertIn(value, schema["properties"][field].get("enum", [schema["properties"][field].get("const")]))
            self.assertNotIn(value + "\n", schema["properties"][field].get("enum", [schema["properties"][field].get("const")]))
        reasons = schema["$defs"]["unavailableReason"]["enum"]
        self.assertIn("no-terminal-usage", reasons)
        self.assertIn("invalid-transport-telemetry", reasons)
        self.assertIn("worker-receipt-binding-failed", reasons)
        self.assertNotIn("no-terminal-usage\n", reasons)

    def test_contract_rejects_open_missing_invalid_and_impossible_records(self):
        bad_values: list[tuple[dict, str]] = []
        value = fixture("worker-usage"); value["extra"] = 1; bad_values.append((value, "$"))
        value = fixture("worker-usage"); del value["observed"]; bad_values.append((value, "$"))
        value = fixture("worker-usage"); value["usage"]["extra"] = 1; bad_values.append((value, "$.usage"))
        value = fixture("worker-usage"); value["observed"] = 1; bad_values.append((value, "$.observed"))
        value = fixture("worker-usage"); value["attempt"] = 1.0; bad_values.append((value, "$.attempt"))
        value = fixture("worker-usage"); value["usage"]["inputTokens"] = True; bad_values.append((value, "inputTokens"))
        value = fixture("worker-usage"); value["usage"]["cachedInputTokens"] = -1; bad_values.append((value, "cachedInputTokens"))
        value = fixture("worker-usage"); value["usage"]["cachedInputTokens"] = 101; bad_values.append((value, "cachedInputTokens"))
        value = fixture("worker-usage"); value["usage"]["reasoningOutputTokens"] = 21; bad_values.append((value, "reasoningOutputTokens"))
        value = fixture("worker-usage"); value["usage"]["cacheWriteInputTokensPresent"] = 0; bad_values.append((value, "cacheWriteInputTokensPresent"))
        value = fixture("worker-usage"); value["observed"] = False; value["unavailableReason"] = "no-terminal-usage"; bad_values.append((value, "$.usage"))
        value = fixture("worker-usage"); value["usage"] = None; bad_values.append((value, "$.usage"))
        value = fixture("worker-usage"); value["usage"] = None; value["observed"] = False; bad_values.append((value, "unavailableReason"))
        value = fixture("worker-usage"); value["usage"] = None; value["observed"] = False; value["unavailableReason"] = "unbounded-free-text"; bad_values.append((value, "unavailableReason"))
        for value, path in bad_values:
            with self.subTest(path=path, value=value):
                self.assert_invalid(value, path)

    def test_every_terminal_status_and_unavailable_reason_is_closed(self):
        for status in (
            "succeeded", "failed", "blocked", "timed-out", "transport-error"
        ):
            value = fixture("worker-usage")
            value["terminalStatus"] = status
            models_api().validate_worker_usage(value)

        for reason in (
            "no-terminal-usage", "malformed-terminal-usage",
            "duplicate-terminal-usage", "conflicting-terminal-usage",
            "invalid-utf8", "input-too-large", "malformed-terminal-record",
            "invalid-transport-telemetry", "worker-receipt-binding-failed",
        ):
            value = fixture("worker-usage")
            value.update({"observed": False, "unavailableReason": reason, "usage": None})
            models_api().validate_worker_usage(value)


if __name__ == "__main__":
    unittest.main()
