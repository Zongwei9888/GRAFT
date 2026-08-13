from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from graft.evidence.reproduction import canonical_reproduction_argv
from graft.schema import (
    EvidenceAwareFeedbackGraph,
    EvidenceCapability,
    EvidenceCapabilityAssessment,
    EvidenceCapabilityDisposition,
    EvidenceRouteAvailability,
    PlannedEvidenceRoute,
    SourceSnapshot,
    VerifierSpec,
)


ELIGIBLE_ORACLE_ORIGINS = frozenset(
    {
        "authoritative_runtime",
        "baseline_repository",
        "requirement_derived_runtime",
    }
)
PORTABLE_DEPENDENCY_ORIGINS = frozenset(
    {"task_environment", "frozen_candidate", "unchanged_baseline"}
)
SUPPORTED_REPRODUCTION_TRANSPORTS = frozenset({"standalone_command"})


def preflight_evidence_capabilities(
    graph: EvidenceAwareFeedbackGraph,
    snapshot: SourceSnapshot,
) -> EvidenceAwareFeedbackGraph:
    """Assess whether each planned verifier can return portable Stop evidence.

    This is a selection-time feasibility check, not correctness evidence.  It
    only accepts routes that the planner declares available and whose authority,
    transport, and dependency origins satisfy the frozen evidence protocol.
    Concrete results are independently checked again after execution.
    """

    capabilities: dict[str, EvidenceCapability] = {}
    duplicates: set[str] = set()
    for capability in graph.evidence_capabilities:
        if capability.verifier_id in capabilities:
            duplicates.add(capability.verifier_id)
        capabilities[capability.verifier_id] = capability

    assessments = tuple(
        _assess_verifier(
            verifier,
            capabilities.get(verifier.verifier_id),
            snapshot,
            duplicate=verifier.verifier_id in duplicates,
        )
        for verifier in graph.verifiers
    )
    return replace(graph, evidence_capability_assessments=assessments)


def eligible_routes(
    graph: EvidenceAwareFeedbackGraph,
    verifier_id: str,
    *,
    oracle_origin: str | None = None,
    transport: str | None = None,
) -> tuple[PlannedEvidenceRoute, ...]:
    assessment = next(
        (
            item
            for item in graph.evidence_capability_assessments
            if item.verifier_id == verifier_id
        ),
        None,
    )
    if (
        assessment is None
        or assessment.disposition != EvidenceCapabilityDisposition.ELIGIBLE
    ):
        return ()
    allowed_ids = set(assessment.eligible_route_ids)
    capability = next(
        (
            item
            for item in graph.evidence_capabilities
            if item.verifier_id == verifier_id
        ),
        None,
    )
    if capability is None:
        return ()
    return tuple(
        route
        for route in capability.routes
        if route.route_id in allowed_ids
        and (oracle_origin is None or route.oracle_origin == oracle_origin)
        and (transport is None or route.transport == transport)
    )


def _assess_verifier(
    verifier: VerifierSpec,
    capability: EvidenceCapability | None,
    snapshot: SourceSnapshot,
    *,
    duplicate: bool,
) -> EvidenceCapabilityAssessment:
    if not verifier.blocking:
        return EvidenceCapabilityAssessment(
            verifier_id=verifier.verifier_id,
            disposition=EvidenceCapabilityDisposition.ADVISORY,
            reasons=("non-blocking verifiers are advisory and do not consume Stop-gating value",),
        )
    if duplicate:
        return EvidenceCapabilityAssessment(
            verifier_id=verifier.verifier_id,
            disposition=EvidenceCapabilityDisposition.INVALID,
            reasons=("multiple evidence-capability declarations target this verifier",),
        )
    if capability is None:
        return EvidenceCapabilityAssessment(
            verifier_id=verifier.verifier_id,
            disposition=EvidenceCapabilityDisposition.INVALID,
            reasons=("the verifier plan omitted its evidence-capability declaration",),
        )

    eligible: list[str] = []
    rejected: list[str] = []
    seen_route_ids: set[str] = set()
    for route in capability.routes:
        if route.route_id in seen_route_ids:
            rejected.append(f"{route.route_id}: duplicate route id")
            continue
        seen_route_ids.add(route.route_id)
        reason = _route_rejection(route)
        if reason is None:
            eligible.append(route.route_id)
        else:
            rejected.append(f"{route.route_id}: {reason}")

    if eligible and verifier.kind == "command":
        expanded = tuple(
            part.replace("{repo}", snapshot.root) for part in verifier.command
        )
        if (
            canonical_reproduction_argv(
                expanded,
                frozen_files=frozenset(snapshot.file_hashes),
                run_root=Path(snapshot.root),
            )
            is None
        ):
            rejected.append(
                "configured command is not replayable from the frozen checkpoint"
            )
            eligible = []

    if eligible:
        return EvidenceCapabilityAssessment(
            verifier_id=verifier.verifier_id,
            disposition=EvidenceCapabilityDisposition.ELIGIBLE,
            eligible_route_ids=tuple(eligible),
            reasons=tuple(rejected),
        )
    return EvidenceCapabilityAssessment(
        verifier_id=verifier.verifier_id,
        disposition=EvidenceCapabilityDisposition.UNAVAILABLE,
        reasons=tuple(rejected) or ("no evidence route was declared",),
    )


def _route_rejection(route: PlannedEvidenceRoute) -> str | None:
    if route.availability != EvidenceRouteAvailability.AVAILABLE:
        return f"availability is {route.availability.value}"
    if route.oracle_origin not in ELIGIBLE_ORACLE_ORIGINS:
        return f"oracle origin {route.oracle_origin!r} cannot authorize blocking feedback"
    if route.transport not in SUPPORTED_REPRODUCTION_TRANSPORTS:
        return f"transport {route.transport!r} is not durably replayable"
    if not route.dependency_origins:
        return "dependency origins are unspecified"
    unsupported = sorted(
        set(route.dependency_origins) - PORTABLE_DEPENDENCY_ORIGINS
    )
    if unsupported:
        return "non-portable dependency origins: " + ", ".join(unsupported)
    return None
