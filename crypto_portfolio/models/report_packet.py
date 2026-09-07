"""Immutable finalized values passed to the user-facing report writer."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from ..metrics_registry import metric_definition
from .decision_packet import SolReview
from .factor_packet import freeze_packet_value, thaw_packet_value
from .time import normalize_timestamp


_REVIEW_TYPES = {"SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW"}
_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}
_FAILURE_STATUSES = {"FAILED", "STALE"}
_FAILURE_STAGES = {"PROVIDER", "WEB_FALLBACK", "EVENT_SCAN", "DERIVED", "VALIDATION", "UNKNOWN"}
_SCRIPT_FAILURE_STATUSES = {"FAILED", "TIMEOUT", "LAUNCH_FAILED"}
_SCRIPT_LOG_SOURCES = {"STDERR", "STDOUT", "LAUNCHER"}
_FAILURE_FIELDS = {
    "asset", "metric_key", "status", "failure_stage", "reason", "error_code", "provider",
    "endpoint", "last_observation_at", "critical", "decision_role", "decision_effect", "attempts",
}
_ATTEMPT_FIELDS = {
    "provider", "status", "error_code", "reason", "endpoint", "status_code", "method", "attempt",
    "exception_class", "detail", "retryable", "log",
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _weights(value: Mapping[str, Any] | None, field: str, *, require_sum: bool = False) -> Mapping[str, float]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, float] = {}
    for raw_symbol, raw_value in value.items():
        symbol = _text(raw_symbol, f"{field} symbol").upper()
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"{field}.{symbol} must be a number")
        number = float(raw_value)
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError(f"{field}.{symbol} must be finite and in [0, 1]")
        result[symbol] = number
    if require_sum and result and not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{field} weights must sum to 1")
    return MappingProxyType(result)


def _scores(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError("scores must be an object")
    def validate(item: Any, path: str) -> Any:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) or not key.strip() for key in item):
                raise ValueError(f"{path} contains an invalid key")
            return freeze_packet_value(
                {str(key): validate(value, f"{path}.{key}") for key, value in item.items()},
                path=path,
            )
        if item is None:
            return None
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{path} must contain numeric scores")
        number = float(item)
        if not math.isfinite(number) or not 0 <= number <= 100:
            raise ValueError(f"{path} must contain scores in [0, 100]")
        return number
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("scores contains an invalid symbol")
        symbol = key.strip().upper()
        if symbol in normalized:
            raise ValueError(f"scores contains duplicate symbol {symbol}")
        normalized[symbol] = validate(item, f"scores.{key}")
    return MappingProxyType(normalized)


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a sequence")
    return tuple(
        freeze_packet_value(item.as_dict() if hasattr(item, "as_dict") else item, path=field)
        for item in value
    )


def _failure_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    from ..providers.http import redact_secrets

    text = str(redact_secrets(value)).strip()
    lowered = text.lstrip().lower()
    if (
        lowered.startswith(("{", "["))
        or "<html" in lowered
        or "<!doctype" in lowered
        or "authorization:" in lowered
        or "cookie:" in lowered
        or "response body" in lowered
        or "raw response" in lowered
        or "content-type:" in lowered
        or '"headers"' in lowered
        or '"body"' in lowered
    ):
        return "[REDACTED]"
    return text


def _failure_endpoint(value: Any, field: str) -> str:
    text = _failure_text(value, field)
    if text == "[REDACTED]":
        return text
    from ..providers.http import redact_url

    try:
        return redact_url(text)
    except ValueError:
        return text


def _failure_log(value: Any, field: str) -> str:
    text = _failure_text(value, field)
    if text == "[REDACTED]":
        return text
    from ..providers.http import redact_log

    return redact_log(text)


def _failed_data_fetches(value: Any, *, review_type: str) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError("failed_data_fetches must be a sequence")
    records: list[Mapping[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"failed_data_fetches[{index}] must be an object")
        unknown = set(raw) - _FAILURE_FIELDS
        if unknown:
            raise ValueError(
                f"failed_data_fetches[{index}] contains unknown fields: {', '.join(sorted(unknown))}"
            )
        missing = {"asset", "metric_key", "status", "failure_stage", "reason", "critical", "decision_role", "decision_effect", "attempts"} - set(raw)
        if missing:
            raise ValueError(
                f"failed_data_fetches[{index}] is missing fields: {', '.join(sorted(missing))}"
            )
        asset = _text(raw["asset"], f"failed_data_fetches[{index}].asset").upper()
        definition = metric_definition(raw["metric_key"])
        if not definition.applies_to(asset):
            raise ValueError(f"metric {definition.key} is not applicable to {asset}")
        identity = (asset, definition.key)
        if identity in identities:
            raise ValueError(f"failed_data_fetches contains duplicate metric {asset}:{definition.key}")
        identities.add(identity)
        status = _text(raw["status"], f"failed_data_fetches[{index}].status").upper()
        if status not in _FAILURE_STATUSES:
            raise ValueError("failed_data_fetches status must be FAILED or STALE")
        stage = _text(raw["failure_stage"], f"failed_data_fetches[{index}].failure_stage").upper()
        if stage not in _FAILURE_STAGES:
            raise ValueError("failed_data_fetches failure_stage is unsupported")
        critical = raw["critical"]
        if not isinstance(critical, bool):
            raise ValueError("failed_data_fetches critical must be boolean")
        if critical != definition.is_critical_for(review_type):
            raise ValueError(f"failed_data_fetches criticality does not match {review_type}")
        decision_role = _text(raw["decision_role"], f"failed_data_fetches[{index}].decision_role").upper()
        if decision_role != definition.decision_role:
            raise ValueError(f"failed_data_fetches decision_role does not match {definition.key}")
        attempts = raw["attempts"]
        if isinstance(attempts, (str, bytes)) or not isinstance(attempts, (list, tuple)):
            raise ValueError(f"failed_data_fetches[{index}].attempts must be a sequence")
        safe_attempts = []
        for attempt_index, raw_attempt in enumerate(attempts):
            if not isinstance(raw_attempt, Mapping):
                raise ValueError(f"failed_data_fetches[{index}].attempts[{attempt_index}] must be an object")
            unknown_attempt = set(raw_attempt) - _ATTEMPT_FIELDS
            if unknown_attempt:
                raise ValueError(
                    f"failed_data_fetches[{index}].attempts[{attempt_index}] contains unknown fields: "
                    + ", ".join(sorted(unknown_attempt))
                )
            if "provider" not in raw_attempt or "status" not in raw_attempt:
                raise ValueError(f"failed_data_fetches[{index}].attempts[{attempt_index}] requires provider and status")
            attempt = {
                "provider": _failure_text(raw_attempt["provider"], "attempt provider"),
                "status": _failure_text(raw_attempt["status"], "attempt status").upper(),
            }
            for field_name in ("error_code", "reason"):
                if raw_attempt.get(field_name) is not None:
                    attempt[field_name] = _failure_text(raw_attempt[field_name], f"attempt {field_name}")
                    if field_name == "error_code":
                        attempt[field_name] = attempt[field_name].upper()
            for field_name in ("exception_class", "detail"):
                if raw_attempt.get(field_name) is not None:
                    attempt[field_name] = _failure_text(raw_attempt[field_name], f"attempt {field_name}")
            if raw_attempt.get("endpoint") is not None:
                attempt["endpoint"] = _failure_endpoint(raw_attempt["endpoint"], "attempt endpoint")
            if raw_attempt.get("method") is not None:
                attempt["method"] = _failure_text(raw_attempt["method"], "attempt method").upper()
            if raw_attempt.get("attempt") is not None:
                attempt_number = raw_attempt["attempt"]
                if (
                    isinstance(attempt_number, bool)
                    or not isinstance(attempt_number, int)
                    or attempt_number < 1
                ):
                    raise ValueError("attempt attempt must be a positive integer")
                attempt["attempt"] = attempt_number
            if raw_attempt.get("retryable") is not None:
                if not isinstance(raw_attempt["retryable"], bool):
                    raise ValueError("attempt retryable must be boolean")
                attempt["retryable"] = raw_attempt["retryable"]
            if "status_code" in raw_attempt and raw_attempt["status_code"] is not None:
                status_code = raw_attempt["status_code"]
                if (
                    isinstance(status_code, bool)
                    or not isinstance(status_code, int)
                    or not 100 <= status_code <= 599
                ):
                    raise ValueError("attempt status_code must be an HTTP status or null")
                attempt["status_code"] = status_code
            if raw_attempt.get("log") is not None:
                attempt["log"] = _failure_log(raw_attempt["log"], "attempt log")
            safe_attempts.append(attempt)
        record: dict[str, Any] = {
            "asset": asset,
            "metric_key": definition.key,
            "status": status,
            "failure_stage": stage,
            "reason": _failure_text(raw["reason"], f"failed_data_fetches[{index}].reason"),
            "critical": critical,
            "decision_role": decision_role,
            "decision_effect": _failure_text(raw["decision_effect"], f"failed_data_fetches[{index}].decision_effect"),
            "attempts": safe_attempts,
        }
        if raw.get("error_code") is not None:
            record["error_code"] = _failure_text(raw["error_code"], "failure error_code").upper()
        if raw.get("provider") is not None:
            record["provider"] = _failure_text(raw["provider"], "failure provider")
        if raw.get("endpoint") is not None:
            record["endpoint"] = _failure_endpoint(raw["endpoint"], "failure endpoint")
        if raw.get("last_observation_at") is not None:
            record["last_observation_at"] = normalize_timestamp(
                raw["last_observation_at"], "last_observation_at"
            )
        records.append(freeze_packet_value(record, path=f"failed_data_fetches[{index}]"))
    records.sort(key=lambda item: (not item["critical"], item["asset"], item["metric_key"]))
    return tuple(records)


def _script_failures(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError("script_failures must be a sequence")
    records: list[Mapping[str, Any]] = []
    required = {"script", "command", "status", "exit_code", "log_source", "log"}
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"script_failures[{index}] must be an object")
        unknown = set(raw) - required
        if unknown:
            raise ValueError(
                f"script_failures[{index}] contains unknown fields: {', '.join(sorted(unknown))}"
            )
        missing = required - set(raw)
        if missing:
            raise ValueError(
                f"script_failures[{index}] is missing fields: {', '.join(sorted(missing))}"
            )
        script = _text(raw["script"], f"script_failures[{index}].script")
        status = _text(raw["status"], f"script_failures[{index}].status").upper()
        if status not in _SCRIPT_FAILURE_STATUSES:
            raise ValueError("script_failures status is unsupported")
        command = raw["command"]
        if isinstance(command, (str, bytes)) or not isinstance(command, (list, tuple)) or not command:
            raise ValueError(f"script_failures[{index}].command must be a non-empty sequence")
        safe_command = [
            _failure_text(item, f"script_failures[{index}].command[{command_index}]")
            for command_index, item in enumerate(command)
        ]
        exit_code = raw["exit_code"]
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
            raise ValueError("script_failures exit_code must be an integer or null")
        if status == "FAILED" and (exit_code is None or exit_code == 0):
            raise ValueError("FAILED script_failures require a non-zero exit_code")
        if status != "FAILED" and exit_code is not None:
            raise ValueError(f"{status} script_failures require a null exit_code")
        log_source = _text(raw["log_source"], f"script_failures[{index}].log_source").upper()
        if log_source not in _SCRIPT_LOG_SOURCES:
            raise ValueError("script_failures log_source is unsupported")
        records.append(freeze_packet_value({
            "script": script,
            "command": safe_command,
            "status": status,
            "exit_code": exit_code,
            "log_source": log_source,
            "log": _failure_log(raw["log"], f"script_failures[{index}].log"),
        }, path=f"script_failures[{index}]"))
    return tuple(records)


@dataclass(frozen=True)
class ReportPacket:
    review_type: str
    market_regime: str
    scores: Mapping[str, Any] = field(default_factory=dict)
    current_weights: Mapping[str, float] = field(default_factory=dict)
    target_weights: Mapping[str, float] = field(default_factory=dict)
    actions: tuple[Any, ...] = ()
    approved_amounts: Mapping[str, float] = field(default_factory=dict)
    execution_zones: Mapping[str, Any] = field(default_factory=dict)
    historical_changes: Mapping[str, Any] = field(default_factory=dict)
    risk_flags: tuple[str, ...] = ()
    sol_review: SolReview | Mapping[str, Any] | None = None
    critical_missing_data: tuple[str, ...] = ()
    data_quality: Mapping[str, Any] = field(default_factory=dict)
    positioning_summaries: Mapping[str, Any] = field(default_factory=dict)
    btc_cycle_summary: Mapping[str, Any] | None = None
    overlay_confidence: str = "LOW"
    overlay_warnings: tuple[str, ...] = ()
    effective_deployment_caps: Mapping[str, float] = field(default_factory=dict)
    failed_data_fetches: tuple[Mapping[str, Any], ...] = ()
    script_failures: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        review = _text(self.review_type, "review_type").upper()
        regime = _text(self.market_regime, "market_regime").upper()
        if review not in _REVIEW_TYPES or regime not in _REGIMES:
            raise ValueError("report packet review_type or market_regime is unsupported")
        object.__setattr__(self, "review_type", review)
        object.__setattr__(self, "market_regime", regime)
        object.__setattr__(self, "scores", _scores(self.scores))
        object.__setattr__(self, "current_weights", _weights(self.current_weights, "current_weights"))
        object.__setattr__(self, "target_weights", _weights(self.target_weights, "target_weights", require_sum=True))
        if not self.target_weights:
            raise ValueError("target_weights must be non-empty")
        amounts: dict[str, float] = {}
        for raw_symbol, raw_amount in (self.approved_amounts or {}).items():
            symbol = _text(raw_symbol, "approved_amounts symbol").upper()
            amount = float(raw_amount)
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("approved amounts must be finite and >= 0")
            amounts[symbol] = amount
        object.__setattr__(self, "approved_amounts", MappingProxyType(amounts))
        actions = _sequence(self.actions, "actions")
        for action in actions:
            if isinstance(action, Mapping):
                name = str(action.get("action", "")).strip().upper()
                if name not in _ACTIONS:
                    raise ValueError("report action is unsupported")
                raw_amount = action.get("amount_usd", action.get("approved_amount_usd", 0.0))
                if isinstance(raw_amount, bool) or not isinstance(raw_amount, (int, float)):
                    raise ValueError("report action amount must be a number")
                amount = float(raw_amount)
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError("report action amount must be finite and >= 0")
                if name in {"HOLD", "WAIT", "NO_TRADE"} and amount != 0:
                    raise ValueError(f"{name} report actions must have zero amount")
        object.__setattr__(self, "actions", actions)
        for field_name in ("execution_zones", "historical_changes", "data_quality"):
            if not isinstance(getattr(self, field_name), Mapping):
                raise ValueError(f"{field_name} must be an object")
            object.__setattr__(self, field_name, freeze_packet_value(getattr(self, field_name), path=field_name))
        object.__setattr__(self, "failed_data_fetches", _failed_data_fetches(self.failed_data_fetches, review_type=review))
        object.__setattr__(self, "script_failures", _script_failures(self.script_failures))
        if not isinstance(self.positioning_summaries, Mapping):
            raise ValueError("positioning_summaries must be an object")
        summaries = {}
        for raw_symbol, summary in self.positioning_summaries.items():
            symbol = _text(raw_symbol, "positioning_summaries symbol").upper()
            if symbol in summaries:
                raise ValueError(f"positioning_summaries contains duplicate symbol {symbol}")
            if hasattr(summary, "as_dict"):
                summary = summary.as_dict()
            if not isinstance(summary, Mapping):
                raise ValueError(f"positioning_summaries.{symbol} must be an object")
            summaries[symbol] = freeze_packet_value(summary, path=f"positioning_summaries.{symbol}")
        object.__setattr__(self, "positioning_summaries", MappingProxyType(summaries))
        if self.btc_cycle_summary is not None:
            if hasattr(self.btc_cycle_summary, "as_dict"):
                object.__setattr__(self, "btc_cycle_summary", self.btc_cycle_summary.as_dict())
            if not isinstance(self.btc_cycle_summary, Mapping):
                raise ValueError("btc_cycle_summary must be an object or null")
            object.__setattr__(self, "btc_cycle_summary", freeze_packet_value(self.btc_cycle_summary, path="btc_cycle_summary"))
        overlay_confidence = _text(self.overlay_confidence, "overlay_confidence").upper()
        if overlay_confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("overlay_confidence must be HIGH, MEDIUM, or LOW")
        object.__setattr__(self, "overlay_confidence", overlay_confidence)
        warnings = tuple(_text(item, "overlay_warnings") for item in self.overlay_warnings)
        if len(warnings) != len(set(warnings)):
            raise ValueError("overlay_warnings must contain unique values")
        object.__setattr__(self, "overlay_warnings", warnings)
        if not isinstance(self.effective_deployment_caps, Mapping):
            raise ValueError("effective_deployment_caps must be an object")
        caps = {}
        for raw_symbol, raw_factor in self.effective_deployment_caps.items():
            symbol = _text(raw_symbol, "effective_deployment_caps symbol").upper()
            if isinstance(raw_factor, bool) or not isinstance(raw_factor, (int, float)):
                raise ValueError("effective_deployment_caps values must be numbers")
            factor = float(raw_factor)
            if not math.isfinite(factor) or not 0 <= factor <= 1:
                raise ValueError("effective_deployment_caps values must be finite and in [0, 1]")
            if symbol in caps:
                raise ValueError(f"effective_deployment_caps contains duplicate symbol {symbol}")
            caps[symbol] = factor
        object.__setattr__(self, "effective_deployment_caps", MappingProxyType(caps))
        for field_name in ("risk_flags", "critical_missing_data"):
            values = tuple(_text(item, field_name) for item in getattr(self, field_name))
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique values")
            object.__setattr__(self, field_name, values)
        review = self.sol_review
        if review is not None and not isinstance(review, SolReview):
            review = SolReview(**dict(review))
        object.__setattr__(self, "sol_review", review)

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_type": self.review_type,
            "market_regime": self.market_regime,
            "scores": thaw_packet_value(self.scores),
            "current_weights": dict(self.current_weights),
            "target_weights": dict(self.target_weights),
            "actions": thaw_packet_value(self.actions),
            "approved_amounts": dict(self.approved_amounts),
            "execution_zones": thaw_packet_value(self.execution_zones),
            "historical_changes": thaw_packet_value(self.historical_changes),
            "risk_flags": list(self.risk_flags),
            "sol_review": self.sol_review.as_dict() if self.sol_review else None,
            "critical_missing_data": list(self.critical_missing_data),
            "data_quality": thaw_packet_value(self.data_quality),
            "failed_data_fetches": thaw_packet_value(self.failed_data_fetches),
            "script_failures": thaw_packet_value(self.script_failures),
            "positioning_summaries": {
                symbol: thaw_packet_value(summary)
                for symbol, summary in self.positioning_summaries.items()
            },
            "btc_cycle_summary": thaw_packet_value(self.btc_cycle_summary) if self.btc_cycle_summary is not None else None,
            "overlay_confidence": self.overlay_confidence,
            "overlay_warnings": list(self.overlay_warnings),
            "effective_deployment_caps": dict(self.effective_deployment_caps),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReportPacket":
        if not isinstance(value, Mapping):
            raise ValueError("report packet must be an object")
        for field_name in ("failed_data_fetches", "script_failures"):
            if field_name not in value:
                raise ValueError(f"report packet is missing {field_name}")
        return cls(**dict(value))


__all__ = ["ReportPacket"]
