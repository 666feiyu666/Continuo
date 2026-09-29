from __future__ import annotations

from statistics import median
from typing import Any

from ..model import MusicProject, instrument_definition


_LEAD_ROLE_MARKERS = (
    "lead",
    "melody",
    "theme",
    "solo",
    "主旋律",
    "主题",
    "独奏",
)


def _is_lead_role(role: str) -> bool:
    normalized = role.casefold()
    return any(marker in normalized for marker in _LEAD_ROLE_MARKERS)


def _line_metrics(events: list[Any], *, window_beats: float) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: (event.start_beat, event.pitch))
    durations = [event.duration_beats for event in ordered]
    positive_gaps = sum(
        following.start_beat > event.start_beat + event.duration_beats + 1e-6
        for event, following in zip(ordered, ordered[1:], strict=False)
    )
    transitions = max(0, len(ordered) - 1)
    return {
        "note_count": len(ordered),
        "window_beats": round(window_beats, 6),
        "attacks_per_beat": round(len(ordered) / window_beats, 6)
        if window_beats > 0
        else 0.0,
        "median_duration_beats": round(float(median(durations)), 6)
        if durations
        else 0.0,
        "short_note_ratio": round(
            sum(duration <= 1.0 + 1e-6 for duration in durations) / len(durations),
            6,
        )
        if durations
        else 0.0,
        "long_note_ratio": round(
            sum(duration >= 2.0 - 1e-6 for duration in durations) / len(durations),
            6,
        )
        if durations
        else 0.0,
        "positive_gap_ratio": round(positive_gaps / transitions, 6)
        if transitions
        else 0.0,
        "slur_connections": sum(
            event.connection_to_next == "slur" for event in ordered
        ),
        "breath_connections": sum(
            event.connection_to_next == "breath" for event in ordered
        ),
    }


def _advisories(metrics: dict[str, Any], *, lead_role: bool) -> list[str]:
    if metrics["note_count"] < 3 or metrics["window_beats"] < 8:
        return []
    advisories: list[str] = []
    if metrics["attacks_per_beat"] < 0.45:
        advisories.append(
            "The line has little phrase-internal motion for its written span."
        )
    if metrics["positive_gap_ratio"] > 0.7:
        advisories.append(
            "Most adjacent notes are separated by silence, so the line may read as "
            "isolated tones instead of a connected phrase."
        )
    if metrics["long_note_ratio"] > 0.5:
        advisories.append(
            "Long values dominate; check that they mark arrivals or cadences rather "
            "than replacing internal melodic motion."
        )
    if lead_role and metrics["note_count"] >= 8 and not (
        metrics["slur_connections"] or metrics["breath_connections"]
    ):
        advisories.append(
            "The lead line has no explicit slur or breath grouping."
        )
    return advisories


def build_symbolic_score_audit(project: MusicProject) -> dict[str, Any]:
    """Return advisory phrase-shape evidence without accepting or rejecting music."""

    tracks: list[dict[str, Any]] = []
    review_targets: list[dict[str, Any]] = []
    for track in project.tracks:
        definition = instrument_definition(track.instrument.id)
        if not definition.monophonic or not track.events:
            continue
        lead_role = _is_lead_role(track.role)
        track_metrics = _line_metrics(
            track.events,
            window_beats=max(
                event.start_beat + event.duration_beats for event in track.events
            )
            - min(event.start_beat for event in track.events),
        )
        track_advisories = _advisories(track_metrics, lead_role=lead_role)
        phrase_results: list[dict[str, Any]] = []
        for phrase in project.phrases:
            events = [
                event for event in track.events if event.phrase_id == phrase.id
            ]
            if not events:
                continue
            metrics = _line_metrics(
                events,
                window_beats=phrase.end_beat - phrase.start_beat,
            )
            advisories = _advisories(metrics, lead_role=lead_role)
            phrase_result = {
                "phrase_id": phrase.id,
                "label": phrase.label,
                "metrics": metrics,
                "advisories": advisories,
            }
            phrase_results.append(phrase_result)
            if advisories:
                review_targets.append(
                    {
                        "track_id": track.id,
                        "phrase_id": phrase.id,
                        "advisories": advisories,
                    }
                )
        tracks.append(
            {
                "track_id": track.id,
                "role": track.role,
                "instrument_id": track.instrument.id,
                "lead_role": lead_role,
                "metrics": track_metrics,
                "advisories": track_advisories,
                "phrases": phrase_results,
            }
        )
    return {
        "schema_version": "1.0",
        "kind": "symbolic-score-advisory",
        "interpretation": (
            "Diagnostics are review evidence, not style-independent quotas or "
            "acceptance checks."
        ),
        "tracks": tracks,
        "review_targets": review_targets,
    }
