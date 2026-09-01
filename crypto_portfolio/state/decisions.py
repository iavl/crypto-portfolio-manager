"""Append-only portfolio decision storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..models.decision import Decision, DecisionStatusEvent
from ..models.evidence import Evidence
from ..models.policy import Policy, policy_from_mapping, policy_hash, resolve_policy
from ..models.time import parse_timestamp
from ._jsonl import append_record, read_records
from .snapshots import runtime_data_dir


_STATUSES = {"PENDING", "CONFIRMED", "NOT_EXECUTED"}


def default_decision_path() -> Path:
    return runtime_data_dir() / "decisions" / "decisions.jsonl"


def _validated_decision(
    decision: Decision | Mapping[str, Any], policy: Policy | None = None
) -> tuple[dict[str, Any], Policy]:
    if isinstance(decision, Mapping):
        if policy is not None:
            resolved = policy
        elif decision.get("resolved_policy") is not None:
            resolved = policy_from_mapping(decision["resolved_policy"])
        else:
            resolved = resolve_policy(decision.get("config"))
        model = Decision.from_mapping(decision)
    elif isinstance(decision, Decision):
        model = decision
        if policy is not None:
            resolved = policy
        elif decision.resolved_policy is not None:
            resolved = policy_from_mapping(decision.resolved_policy)
        else:
            resolved = resolve_policy()
    else:
        raise ValueError("decision must be a Decision or mapping")
    parse_timestamp(model.timestamp)
    if model.policy_version != resolved.policy_version:
        raise ValueError("decision policy_version does not match resolved policy")
    expected_hash = policy_hash(resolved)
    if model.policy_hash is not None and model.policy_hash != expected_hash:
        raise ValueError("decision policy_hash does not match resolved policy")
    if any(not isinstance(item, Evidence) for item in model.evidence):
        raise ValueError("persisted decision evidence must contain complete Evidence objects")
    record = model.as_dict()
    record["policy_hash"] = expected_hash
    record["resolved_policy"] = resolved.as_dict()
    record["policy_version"] = resolved.policy_version
    return record, resolved


def append_decision(
    decision: Mapping[str, Any] | Decision,
    path: str | Path | None = None,
    *,
    policy: Policy | None = None,
) -> Path:
    record, _ = _validated_decision(decision, policy)
    destination = Path(path or default_decision_path())
    existing = read_records(destination)
    if record.get("decision_id") and any(item.get("decision_id") == record["decision_id"] for item in existing):
        raise ValueError(f"duplicate decision_id {record['decision_id']}")
    comparable = {key: value for key, value in record.items() if key != "decision_id"}
    if any(
        {key: value for key, value in item.items() if key != "decision_id"} == comparable
        for item in existing
    ):
        raise ValueError("duplicate decision record")
    if not record.get("decision_id"):
        from uuid import uuid4

        record["decision_id"] = str(uuid4())
    return append_record(destination, record)


def default_status_event_path() -> Path:
    return runtime_data_dir() / "decisions" / "status-events.jsonl"


def append_status_event(
    event: DecisionStatusEvent | Mapping[str, Any], path: str | Path | None = None
) -> Path:
    model = event if isinstance(event, DecisionStatusEvent) else DecisionStatusEvent(**event)
    destination = Path(path or default_status_event_path())
    return append_record(destination, model.as_dict())


def read_status_events(path: str | Path | None = None) -> list[dict[str, Any]]:
    return read_records(path or default_status_event_path())


def reconstruct_decision_status(
    decision: Decision | Mapping[str, Any], events: list[DecisionStatusEvent | Mapping[str, Any]]
) -> Decision:
    model = decision if isinstance(decision, Decision) else Decision.from_mapping(decision)
    if not model.decision_id:
        raise ValueError("decision_id is required to reconstruct status")
    parsed = [
        event if isinstance(event, DecisionStatusEvent) else DecisionStatusEvent(**event)
        for event in events
    ]
    matching = sorted(
        (event for event in parsed if event.decision_id == model.decision_id),
        key=lambda event: parse_timestamp(event.timestamp),
    )
    if not matching:
        return model
    from dataclasses import replace

    return replace(model, status=matching[-1].status)


def read_decisions(path: str | Path | None = None) -> list[dict[str, Any]]:
    return read_records(path or default_decision_path())


__all__ = [
    "append_decision",
    "append_status_event",
    "default_decision_path",
    "default_status_event_path",
    "read_decisions",
    "read_status_events",
    "reconstruct_decision_status",
]
