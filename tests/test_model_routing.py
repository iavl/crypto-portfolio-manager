import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from crypto_portfolio.model_routing import (
    LLM_STAGES,
    PYTHON_STAGES,
    ModelSpec,
    RoutingError,
    RuntimeCapabilities,
    load_model_routing,
    resolve_stage_route,
    routing_metadata,
    validate_model_routing,
)


ROOT = Path(__file__).resolve().parents[1]


class ModelRoutingTests(unittest.TestCase):
    def test_default_and_builtin_profiles(self):
        with patch.dict(os.environ, {"CRYPTO_PORTFOLIO_MODEL_PROFILE": ""}, clear=False):
            routing = load_model_routing()
        self.assertEqual(routing.routing_policy_version, 2)
        self.assertEqual(routing.profile, "balanced")
        self.assertEqual(routing.preset_for_stage("factor_semantic_analysis"), "terra_medium")
        self.assertEqual(load_model_routing(profile="efficient").preset_for_stage("source_conflict_resolution"), "terra_low")
        self.assertEqual(load_model_routing(profile="efficient").preset_for_stage("report_generation"), "luna_max")
        quality = load_model_routing(profile="quality")
        self.assertEqual(quality.preset_for_stage("major_event_analysis"), "sol_xhigh")
        session = load_model_routing(profile="session_compatible")
        self.assertEqual(session.preset_for_stage("report_generation"), "current_session")
        for stage in PYTHON_STAGES:
            self.assertEqual(routing.model_for_stage(stage), "PYTHON")

    def test_luna_max_only_and_reasoning_validation(self):
        self.assertEqual(ModelSpec("luna", "openai", "LUNA", "luna-id", "max").reasoning_effort, "max")
        for effort in ("none", "low", "medium", "high", "xhigh"):
            with self.subTest(effort=effort), self.assertRaises(RoutingError):
                ModelSpec("luna", "openai", "LUNA", "luna-id", effort)
        with self.assertRaises(RoutingError):
            ModelSpec("luna", "openai", "LUNA", "luna-id", "max", ("low",))

    def test_profile_inheritance_and_python_ownership(self):
        routing = load_model_routing()
        custom = dict(routing.as_dict())
        custom["profiles"] = dict(custom["profiles"])
        custom["profiles"]["child"] = {
            "extends": "balanced",
            "stages": {"factor_semantic_analysis": "terra_high"},
        }
        custom["default_profile"] = "child"
        self.assertEqual(validate_model_routing(custom).preset_for_stage("factor_semantic_analysis"), "terra_high")
        invalid = json.loads(json.dumps(custom))
        invalid["profiles"]["child"]["stages"]["technical"] = "terra_high"
        with self.assertRaisesRegex(RoutingError, "technical.*PYTHON"):
            validate_model_routing(invalid)
        invalid = json.loads(json.dumps(custom))
        invalid["profiles"]["child"]["extends"] = "missing"
        with self.assertRaisesRegex(RoutingError, "Unknown profile"):
            validate_model_routing(invalid)
        cycle = json.loads(json.dumps(custom))
        cycle["profiles"]["child"]["extends"] = "cycle"
        cycle["profiles"]["cycle"] = {"extends": "child", "stages": {}}
        with self.assertRaisesRegex(RoutingError, "inheritance cycle"):
            validate_model_routing(cycle)

    def test_local_override_and_profile_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model-routing.json"
            path.write_text(
                json.dumps(
                    {
                        "active_profile": "local",
                        "profiles": {
                            "local": {
                                "extends": "balanced",
                                "stages": {"report_generation": "terra_high"},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "CRYPTO_PORTFOLIO_MODEL_CONFIG": str(path),
                    "CRYPTO_PORTFOLIO_MODEL_PROFILE": "quality",
                },
            ):
                self.assertEqual(load_model_routing().profile, "quality")
                self.assertEqual(load_model_routing(profile="local").profile, "local")
                self.assertEqual(load_model_routing(profile="local").preset_for_stage("report_generation"), "terra_high")
            self.assertFalse(path.with_suffix(".bak").exists())

    def test_run_level_override_is_explicit(self):
        routing = load_model_routing(
            profile="balanced",
            run_overrides={"major_event_analysis": "sol_xhigh"},
        )
        self.assertEqual(routing.preset_for_stage("major_event_analysis"), "sol_xhigh")
        route = resolve_stage_route(
            "major_event_analysis",
            routing=routing,
            runtime_capabilities=RuntimeCapabilities("CODEX", True, True),
        )
        self.assertEqual(route.requested_preset, "sol_xhigh")
        self.assertEqual(route.effective_reasoning_effort, "xhigh")

    def test_custom_local_preset_is_resolved_by_injected_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model-routing.json"
            path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "custom": {
                                "extends": "balanced",
                                "stages": {"factor_semantic_analysis": "my_model"},
                            }
                        },
                        "active_profile": "custom",
                        "models": {
                            "my_model": {
                                "provider": "openai",
                                "family": "CUSTOM",
                                "model": "custom-model-id",
                                "reasoning_effort": "medium",
                                "supported_reasoning_efforts": ["low", "medium", "high"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            routing = load_model_routing(path)
            route = resolve_stage_route(
                "factor_semantic_analysis",
                routing=routing,
                runtime_capabilities=RuntimeCapabilities(
                    "OPENAI_API", True, True, ("custom-model-id",)
                ),
            )
            self.assertEqual(route.effective_model, "custom-model-id")
            self.assertFalse(route.fallback_used)

    def test_v1_is_read_without_writeback(self):
        value = {
            "routing_policy_version": 1,
            "mode": "AUTO",
            "luna_policy": "LUNA_MAX_ONLY",
            "stages": {
                "metric_collection": "LUNA_MAX",
                "factor_semantic_analysis": "TERRA",
                "technical": "PYTHON",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v1.json"
            original = json.dumps(value, indent=2)
            path.write_text(original, encoding="utf-8")
            routing = load_model_routing(path)
            self.assertEqual(routing.routing_policy_version, 1)
            self.assertEqual(routing.preset_for_stage("factor_semantic_analysis"), "terra_medium")
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            schema = json.loads(
                (ROOT / "schemas" / "model-routing.schema.json").read_text(encoding="utf-8")
            )
            errors = list(Draft202012Validator(schema).iter_errors(value))
            self.assertEqual(errors, [])

    def test_capability_resolution_never_fakes_switching(self):
        routing = load_model_routing()
        chatgpt = RuntimeCapabilities("CHATGPT", False, False)
        route = resolve_stage_route(
            "factor_semantic_analysis", routing=routing, runtime_capabilities=chatgpt
        )
        self.assertEqual(route.requested_model, "gpt-5.6-terra")
        self.assertEqual(route.effective_model, "CURRENT_SESSION")
        self.assertEqual(route.effective_reasoning_effort, "inherit")
        self.assertTrue(route.fallback_used)
        self.assertIn("per-stage model selection", route.fallback_reason)

        capable = RuntimeCapabilities(
            "CODEX",
            True,
            True,
            ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"),
            {
                "gpt-5.6-luna": ("max",),
                "gpt-5.6-terra": ("low", "medium", "high"),
                "gpt-5.6-sol": ("medium", "high", "xhigh"),
            },
        )
        route = resolve_stage_route(
            "factor_semantic_analysis", routing=routing, runtime_capabilities=capable
        )
        self.assertEqual(route.effective_model, "gpt-5.6-terra")
        self.assertEqual(route.effective_reasoning_effort, "medium")
        self.assertFalse(route.fallback_used)

    def test_unavailable_model_and_effort_are_explicit(self):
        routing = load_model_routing()
        unavailable = RuntimeCapabilities("CODEX", True, True, ("gpt-5.6-sol",))
        route = resolve_stage_route(
            "factor_semantic_analysis", routing=routing, runtime_capabilities=unavailable
        )
        self.assertEqual(route.effective_model, "CURRENT_SESSION")
        self.assertTrue(route.fallback_used)
        limited = RuntimeCapabilities(
            "CODEX",
            True,
            True,
            ("gpt-5.6-terra",),
            {"gpt-5.6-terra": ("low",)},
        )
        route = resolve_stage_route(
            "factor_semantic_analysis", routing=routing, runtime_capabilities=limited
        )
        self.assertEqual(route.effective_model, "gpt-5.6-terra")
        self.assertEqual(route.effective_reasoning_effort, "inherit")
        self.assertTrue(route.fallback_used)
        self.assertIn("reasoning effort", route.fallback_reason)

    def test_metadata_hash_and_route_inventory(self):
        routing = load_model_routing()
        capable = RuntimeCapabilities("CODEX", True, True)
        metadata = routing_metadata(routing=routing, runtime_capabilities=capable)
        self.assertEqual(metadata["profile"], "balanced")
        self.assertEqual(metadata["runtime"], "CODEX")
        self.assertEqual(len(metadata["stages"]), len(PYTHON_STAGES | LLM_STAGES))
        self.assertEqual(metadata["stages"]["factor_semantic_analysis"]["effective_model"], "gpt-5.6-terra")
        self.assertEqual(len(metadata["config_hash"]), 64)
        self.assertNotIn("reasoning", json.dumps(metadata).lower().replace("reasoning_effort", ""))
        self.assertNotEqual(routing.config_hash, load_model_routing(profile="quality").config_hash)

    def test_session_metadata_is_valid_decision_metadata(self):
        from crypto_portfolio.models.decision import Decision

        metadata = routing_metadata(routing=load_model_routing(profile="session_compatible"))
        decision = Decision(
            "2026-09-02T00:00:00Z",
            "NORMAL",
            1,
            {"BTC": 1.0},
            {"BTC": 1.0},
            routing_metadata=metadata,
        )
        self.assertEqual(decision.routing_metadata["profile"], "session_compatible")

    def test_cli_is_read_only_and_no_network(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "model_routing.py"), "--validate", "--list-profiles"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("valid: routing policy v2", result.stdout)
        self.assertIn("balanced", result.stdout)


if __name__ == "__main__":
    unittest.main()
