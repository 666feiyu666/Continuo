from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import wave
from array import array
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuo.cli import main
from continuo.composition import (
    ARRANGEMENT_STAGE,
    ARRANGEMENT_TOOL_NAMES,
    CORE_STAGE,
    CORE_TOOL_NAMES,
    MusicToolRuntime,
    RecordedProvider,
    parse_model_plan,
    tool_manifest,
)
from continuo.composition.provider import OpenAIResponsesProvider
from continuo.composition.schema import music_plan_schema
from continuo.model import (
    DomainValidationError,
    INSTRUMENT_CATALOG,
    SUPPORTED_AUTOMATION_PARAMETERS,
    SUPPORTED_NOTE_CONNECTIONS,
    SUPPORTED_INSTRUMENT_IDS,
    instrument_definition,
    score_sha256,
)
from continuo.rendering import RenderReport
from continuo.rendering.articulation import realized_duration, realized_velocity
from continuo.rendering.midi import (
    GM_PROGRAM_BY_INSTRUMENT,
    realized_midi_end_beat,
    write_midi,
)
from continuo.rendering.soundfont import (
    SoundFontPreset,
    SoundFontProfile,
    available_instrument_ids,
    deterministic_soundfont_mapping,
    parse_fluidsynth_preset_listing,
    parse_soundfont_mapping,
    resolve_soundfont_mapping,
    validate_soundfont_compatibility,
    validate_soundfont_mapping,
)
from continuo.skills import SkillRegistry
from continuo.workflow import (
    AgentRuntime,
    ArtifactStore,
    CaseValidationError,
    ResearchCase,
    RunPolicy,
)


ROOT = Path(__file__).resolve().parents[1]


def _short_plan() -> dict:
    return {
        "schema_version": "1.0",
        "brief": {"style": "abstract electronic", "duration_seconds": 1},
        "rationale": "A generic non-jazz path test.",
        "tool_calls": [
            {
                "name": "create_project",
                "arguments": {
                    "title": "Path Test",
                    "duration_seconds": 1,
                    "tempo_bpm": 120,
                    "meter_numerator": 4,
                    "meter_denominator": 4,
                    "swing": 0.5,
                    "seed": 7,
                },
            },
            {
                "name": "add_track",
                "arguments": {
                    "id": "tone",
                    "name": "Tone",
                    "role": "texture",
                    "instrument": {
                        "id": "synth_pad_warm",
                    },
                },
            },
            {
                "name": "add_note",
                "arguments": {
                    "track_id": "tone",
                    "start_beat": 0,
                    "duration_beats": 1,
                    "pitch": 60,
                    "velocity": 0.7,
                    "section_id": None,
                    "phrase_id": None,
                    "articulation": "normal",
                },
            },
        ],
    }


def _test_soundfont_profile() -> SoundFontProfile:
    return SoundFontProfile(
        id="test.sf2",
        sha256="test-sha256",
        presets=(
            SoundFontPreset(
                id="000-089",
                bank=0,
                program=89,
                name="Warm Pad",
                is_percussion=False,
            ),
            SoundFontPreset(
                id="000-067",
                bank=0,
                program=67,
                name="Baritone Sax",
                is_percussion=False,
            ),
            SoundFontPreset(
                id="000-066",
                bank=0,
                program=66,
                name="Tenor Sax",
                is_percussion=False,
            ),
            SoundFontPreset(
                id="000-073",
                bank=0,
                program=73,
                name="Flute",
                is_percussion=False,
            ),
            SoundFontPreset(
                id="000-071",
                bank=0,
                program=71,
                name="Clarinet",
                is_percussion=False,
            ),
            SoundFontPreset(
                id="000-041",
                bank=0,
                program=41,
                name="Viola",
                is_percussion=False,
            ),
            SoundFontPreset(
                id="000-042",
                bank=0,
                program=42,
                name="Cello",
                is_percussion=False,
            ),
            SoundFontPreset(
                id="011-089",
                bank=11,
                program=89,
                name="Solar Wind",
                is_percussion=False,
            ),
            SoundFontPreset(
                id="128-040",
                bank=128,
                program=40,
                name="Brush Kit",
                is_percussion=True,
            ),
        ),
    )


class _TestSoundFontRenderer:
    name = "fluidsynth-soundfont"

    def soundfont_profile(self) -> SoundFontProfile:
        return _test_soundfont_profile()

    def render(self, project, output_path, soundfont_mapping):
        validate_soundfont_mapping(project, soundfont_mapping, self.soundfont_profile())
        sample_rate = 44_100
        frames = round(project.duration_seconds * sample_rate)
        samples = array("h", [2400, -2400]) * frames
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(samples.tobytes())
        level = 2400 / 32767.0
        return RenderReport(
            backend=self.name,
            sample_rate=sample_rate,
            channels=2,
            frames=frames,
            duration_seconds=project.duration_seconds,
            peak=level,
            rms=level,
            performance_realization={
                "slur": "MIDI note-overlap fallback; transition samples are not declared",
                "expression": "MIDI CC11 emitted; preset response is not profiled",
                "breath": "MIDI CC2 emitted; preset response is not profiled",
                "modulation": "MIDI CC1 emitted; preset response is not profiled",
                "pitch_bend": "MIDI pitch wheel emitted; bend range is not profiled",
            },
        )


def _mapping_payload(preset_id: str = "011-089") -> dict:
    return {
        "schema_version": "1.0",
        "master_gain": 0.8,
        "reverb_enabled": True,
        "assignments": [
            {
                "track_id": "tone",
                "preset_id": preset_id,
                "reason": "The alternate pad better fits the role.",
            }
        ],
    }


def _arrangement_plan() -> dict:
    return {
        "schema_version": "1.0",
        "brief": {"style": "arrangement", "duration_seconds": 1},
        "rationale": "Shape the existing core and finalize it.",
        "tool_calls": [
            {
                "name": "add_automation",
                "arguments": {
                    "track_id": "tone",
                    "beat": 0,
                    "parameter": "gain",
                    "value": 0.9,
                },
            },
            {"name": "finalize_project", "arguments": {}},
        ],
    }


def _complete_score_plan() -> dict:
    return {
        "schema_version": "1.0",
        "brief": {"style": "lyrical", "duration_seconds": 4},
        "rationale": "The notation itself carries the musical decisions.",
        "tool_calls": [
            {
                "name": "create_project",
                "arguments": {
                    "title": "Complete Score",
                    "duration_seconds": 4,
                    "tempo_bpm": 60,
                    "meter_numerator": 4,
                    "meter_denominator": 4,
                    "swing": 0.5,
                    "seed": 11,
                },
            },
            {"name": "add_section", "arguments": {"id": "a", "label": "Statement", "start_beat": 0, "end_beat": 2}},
            {"name": "add_section", "arguments": {"id": "b", "label": "Variation", "start_beat": 2, "end_beat": 4}},
            {"name": "add_key_region", "arguments": {"id": "key-a", "section_id": "a", "start_beat": 0, "end_beat": 2, "tonic": "C", "mode": "major"}},
            {"name": "add_key_region", "arguments": {"id": "key-b", "section_id": "b", "start_beat": 2, "end_beat": 4, "tonic": "D", "mode": "dorian"}},
            {"name": "add_phrase", "arguments": {"id": "p1", "label": "Motif crossing the formal boundary", "start_beat": 0, "end_beat": 3, "motif_id": "m1", "variation_of": None}},
            {"name": "add_phrase", "arguments": {"id": "p2", "label": "Motif varied", "start_beat": 3, "end_beat": 4, "motif_id": "m1", "variation_of": "p1"}},
            {"name": "add_track", "arguments": {"id": "lead", "name": "Lead", "role": "melody", "instrument": {"id": "tenor_sax"}}},
            {"name": "add_note", "arguments": {"track_id": "lead", "start_beat": 0, "duration_beats": 1, "pitch": 60, "velocity": 0.55, "section_id": "a", "phrase_id": "p1", "articulation": "legato"}},
            {"name": "add_note", "arguments": {"track_id": "lead", "start_beat": 1, "duration_beats": 1.5, "pitch": 62, "velocity": 0.65, "section_id": "a", "phrase_id": "p1", "articulation": "accent"}},
            {"name": "add_note", "arguments": {"track_id": "lead", "start_beat": 2.5, "duration_beats": 0.5, "pitch": 63, "velocity": 0.62, "section_id": "b", "phrase_id": "p1", "articulation": "tenuto"}},
            {"name": "add_note", "arguments": {"track_id": "lead", "start_beat": 3, "duration_beats": 1, "pitch": 60, "velocity": 0.48, "section_id": "b", "phrase_id": "p2", "articulation": "normal"}},
        ],
    }


class PlanningTests(unittest.TestCase):
    def test_score_articulation_has_one_midi_realization(self) -> None:
        self.assertAlmostEqual(realized_duration(2.0, "staccato"), 1.1)
        self.assertAlmostEqual(realized_duration(1.0, "legato"), 1.08)
        self.assertAlmostEqual(realized_velocity(0.5, "accent"), 0.56)

    def test_baritone_sax_fixture_compiles_connections_and_controllers(self) -> None:
        raw = (
            ROOT / "tests" / "fixtures" / "baritone_sax_legato_8bars.plan.json"
        ).read_text(encoding="utf-8")
        project = MusicToolRuntime().apply_plan(parse_model_plan(raw))
        project.validate(forbid_vocals=True)
        track = project.tracks[0]
        slurred = next(
            (event, following)
            for event, following in zip(track.events, track.events[1:], strict=False)
            if event.connection_to_next == "slur"
        )

        self.assertEqual(track.instrument.id, "baritone_sax")
        self.assertGreater(
            realized_midi_end_beat(*slurred),
            slurred[1].start_beat,
        )
        self.assertEqual(
            {point.parameter for point in track.automation},
            {"expression", "breath", "modulation", "pitch_bend"},
        )
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "baritone-sax.mid"
            write_midi(project, midi_path)
            midi = midi_path.read_bytes()

        self.assertIn(bytes([0xB0, 11]), midi)
        self.assertIn(bytes([0xB0, 2]), midi)
        self.assertIn(bytes([0xB0, 1]), midi)
        self.assertIn(bytes([0xE0]), midi)

    def test_bolero_fixture_preserves_reference_theme_and_orchestral_roles(self) -> None:
        raw = (
            ROOT / "tests" / "fixtures" / "bolero_electronic_excerpt.plan.json"
        ).read_text(encoding="utf-8")
        project = MusicToolRuntime().apply_plan(parse_model_plan(raw))
        project.validate(forbid_vocals=True)
        tracks = {track.id: track for track in project.tracks}
        flute = tracks["flute"].events
        clarinet = tracks["clarinet"].events

        self.assertEqual(project.meter_numerator, 3)
        self.assertEqual(project.total_beats, 114)
        self.assertEqual(len(project.tracks), 5)
        self.assertEqual(len(flute), 100)
        self.assertEqual(len(clarinet), 100)
        self.assertEqual(
            (flute[0].start_beat, flute[0].duration_beats, flute[0].pitch),
            (12, 1.5, 72),
        )
        self.assertEqual(
            (flute[-1].start_beat, flute[-1].duration_beats, flute[-1].pitch),
            (59.75, 0.25, 62),
        )
        self.assertEqual(
            (clarinet[0].start_beat, clarinet[0].duration_beats, clarinet[0].pitch),
            (66, 1.5, 72),
        )
        self.assertEqual(len(tracks["snare"].events), 456)
        self.assertEqual(
            {track.instrument.id for track in project.tracks},
            {"flute", "clarinet", "viola", "cello", "snare_drum"},
        )
        self.assertEqual(
            sum(
                event.connection_to_next == "slur"
                for track in project.tracks
                for event in track.events
            ),
            196,
        )
        self.assertEqual(
            {point.parameter for track in project.tracks for point in track.automation},
            {"expression", "modulation"},
        )

    def test_georgia_fixture_preserves_reference_form_and_rhythmic_profile(self) -> None:
        raw = (
            ROOT / "tests" / "fixtures" / "georgia_on_my_mind_reference.plan.json"
        ).read_text(encoding="utf-8")
        project = MusicToolRuntime().apply_plan(parse_model_plan(raw))
        project.validate(forbid_vocals=True)
        tracks = {track.id: track for track in project.tracks}
        melody = tracks["baritone_sax"].events

        self.assertEqual(project.meter_numerator, 2)
        self.assertEqual(project.meter_denominator, 2)
        self.assertEqual(project.total_beats, 160)
        self.assertEqual(
            [section.id for section in project.sections],
            ["intro", "a1", "a2", "bridge", "a3"],
        )
        self.assertEqual(len(project.tracks), 2)
        self.assertEqual(len(melody), 90)
        self.assertEqual(len(tracks["piano"].events), 545)
        self.assertEqual(
            (min(note.pitch for note in melody), max(note.pitch for note in melody)),
            (50, 64),
        )
        self.assertEqual(sum(note.duration_beats <= 1 for note in melody), 68)
        self.assertEqual(sum(note.connection_to_next == "slur" for note in melody), 83)
        self.assertEqual(sum(note.connection_to_next == "breath" for note in melody), 3)

    def test_connections_preserve_intent_without_gap_thresholds(self) -> None:
        payload = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "baritone_sax_legato_8bars.plan.json"
            ).read_text(encoding="utf-8")
        )
        note_sequence = next(
            call for call in payload["tool_calls"] if call["name"] == "add_note_sequence"
        )
        note_sequence["arguments"]["notes"][0]["duration_beats"] = 0.1
        note_sequence["arguments"]["notes"][1]["phrase_id"] = "answer"
        breath = next(
            note
            for note in note_sequence["arguments"]["notes"]
            if note["connection_to_next"] == "breath"
        )
        following = note_sequence["arguments"]["notes"][
            note_sequence["arguments"]["notes"].index(breath) + 1
        ]
        breath["duration_beats"] = following["start_beat"] - breath["start_beat"]
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(payload))
        )

        project.validate()
        track = project.tracks[0]
        self.assertGreater(
            realized_midi_end_beat(track.events[0], track.events[1]),
            track.events[1].start_beat,
        )
        breath_index = next(
            index
            for index, event in enumerate(track.events)
            if event.connection_to_next == "breath"
        )
        self.assertLess(
            realized_midi_end_beat(
                track.events[breath_index], track.events[breath_index + 1]
            ),
            track.events[breath_index + 1].start_beat,
        )

    def test_phrase_membership_allows_pickups_and_releases(self) -> None:
        payload = _complete_score_plan()
        phrase = next(
            call
            for call in payload["tool_calls"]
            if call["name"] == "add_phrase" and call["arguments"]["id"] == "p2"
        )
        phrase["arguments"]["start_beat"] = 3.5
        notes = [
            call
            for call in payload["tool_calls"]
            if call["name"] == "add_note"
        ]
        notes[2]["arguments"]["duration_beats"] = 0.75
        notes[3]["arguments"]["start_beat"] = 3.25
        notes[3]["arguments"]["duration_beats"] = 0.75

        project = MusicToolRuntime().apply_plan(parse_model_plan(json.dumps(payload)))

        project.validate()

    def test_performance_vocabulary_is_bounded(self) -> None:
        self.assertEqual(SUPPORTED_NOTE_CONNECTIONS, ("separate", "slur", "breath"))
        self.assertEqual(
            SUPPORTED_AUTOMATION_PARAMETERS,
            ("gain", "pan", "expression", "breath", "modulation", "pitch_bend"),
        )

    def test_fluidsynth_listing_builds_actual_preset_profile(self) -> None:
        presets = parse_fluidsynth_preset_listing(
            "Type 'help' for help topics.\n"
            "> 000-000 Grand Piano\n"
            "011-089 Solar Wind\n"
            "128-040 Brush Kit\n"
            ">"
        )
        self.assertEqual(
            [(item.id, item.name) for item in presets],
            [
                ("000-000", "Grand Piano"),
                ("011-089", "Solar Wind"),
                ("128-040", "Brush Kit"),
            ],
        )
        self.assertFalse(presets[1].is_percussion)
        self.assertTrue(presets[2].is_percussion)

    def test_deterministic_soundfont_mapping_compiles_score_to_midi(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        score_before = project.to_dict()
        mapping = deterministic_soundfont_mapping(project, _test_soundfont_profile())
        self.assertEqual(project.to_dict(), score_before)
        self.assertEqual(
            (mapping.tracks[0].preset.bank, mapping.tracks[0].preset.program),
            (0, 89),
        )
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "deterministically-mapped.mid"
            write_midi(project, midi_path, mapping)
            raw = midi_path.read_bytes()
        self.assertIn(bytes([0xB0, 0, 0]), raw)
        self.assertIn(bytes([0xC0, 89]), raw)

    def test_soundfont_mapping_is_rejected_after_score_mutation(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        mapping = resolve_soundfont_mapping(
            project,
            parse_soundfont_mapping(json.dumps(_mapping_payload())),
            _test_soundfont_profile(),
        )
        project.tracks[0].events[0].pitch = 61
        with self.assertRaisesRegex(DomainValidationError, "does not match"):
            validate_soundfont_mapping(
                project,
                mapping,
                _test_soundfont_profile(),
            )

    def test_soundfont_mapping_rejects_percussion_kit_for_pitched_track(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        request = parse_soundfont_mapping(
            json.dumps(_mapping_payload("128-040"))
        )
        with self.assertRaises(DomainValidationError):
            resolve_soundfont_mapping(
                project,
                request,
                _test_soundfont_profile(),
            )

    def test_soundfont_track_limit_is_not_a_score_invariant(self) -> None:
        payload = _short_plan()
        for index in range(1, 16):
            track_id = f"tone-{index}"
            payload["tool_calls"].extend(
                [
                    {
                        "name": "add_track",
                        "arguments": {
                            "id": track_id,
                            "name": track_id,
                            "role": "layer",
                            "instrument": {"id": "synth_pad_warm"},
                        },
                    },
                    {
                        "name": "add_note",
                        "arguments": {
                            "track_id": track_id,
                            "start_beat": 0,
                            "duration_beats": 1,
                            "pitch": 60 + index % 12,
                            "velocity": 0.4,
                            "section_id": None,
                            "phrase_id": None,
                            "articulation": "normal",
                        },
                    },
                ]
            )
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(payload))
        )
        project.validate()
        with self.assertRaisesRegex(DomainValidationError, "SoundFont rendering"):
            validate_soundfont_compatibility(project, _test_soundfont_profile())

    def test_composition_schema_is_limited_to_soundfont_capabilities(self) -> None:
        schema = music_plan_schema(
            allowed_instrument_ids=("synth_pad_warm", "baritone_sax")
        )
        calls = schema["properties"]["tool_calls"]["items"]["anyOf"]
        add_track = next(
            call
            for call in calls
            if call["properties"]["name"]["const"] == "add_track"
        )
        self.assertEqual(
            add_track["properties"]["arguments"]["properties"]["instrument"]
            ["properties"]["id"]["enum"],
            ["synth_pad_warm", "baritone_sax"],
        )

    def test_default_skill_registry_only_activates_composition(self) -> None:
        registry = SkillRegistry.default()
        soundfont_selection = registry.resolve(renderer_name="fluidsynth-soundfont")
        soundfont_ids = [
            item["id"]
            for item in soundfont_selection.manifest()
        ]

        self.assertEqual(
            soundfont_ids,
            ["conservatory-composition"],
        )
        self.assertNotIn("SoundFont preset mapper", soundfont_selection.instructions)

    def test_soundfont_capabilities_are_derived_from_real_presets(self) -> None:
        available = available_instrument_ids(_test_soundfont_profile())
        self.assertIn("synth_pad_warm", available)
        self.assertIn("baritone_sax", available)
        self.assertIn("snare_drum", available)
        self.assertNotIn("acoustic_grand_piano", available)

    def test_generated_notes_outside_timeline_are_rejected_without_clipping(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][2]["arguments"].update(
            {"start_beat": 1.75, "duration_beats": 1}
        )
        payload["tool_calls"].append(
            {
                "name": "add_note",
                "arguments": {
                    "track_id": "tone",
                    "start_beat": 2,
                    "duration_beats": 0.5,
                    "pitch": 62,
                    "velocity": 0.7,
                    "section_id": None,
                    "phrase_id": None,
                    "articulation": "normal",
                },
            }
        )

        project = MusicToolRuntime().apply_plan(parse_model_plan(json.dumps(payload)))

        self.assertEqual(len(project.tracks[0].events), 2)
        self.assertAlmostEqual(project.tracks[0].events[0].duration_beats, 1)
        with self.assertRaises(DomainValidationError):
            project.validate()

    def test_project_duration_has_a_five_minute_hard_limit(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][0]["arguments"]["duration_seconds"] = 301
        project = MusicToolRuntime().apply_plan(parse_model_plan(json.dumps(payload)))

        with self.assertRaisesRegex(DomainValidationError, "duration_seconds"):
            project.validate()

    def test_complete_score_encodes_form_modulation_and_variation(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_complete_score_plan()))
        )
        project.validate()
        self.assertEqual([item.id for item in project.sections], ["a", "b"])
        self.assertEqual(project.key_regions[1].tonic, "D")
        self.assertGreater(project.phrases[0].end_beat, project.sections[0].end_beat)
        self.assertGreater(
            project.tracks[0].events[1].start_beat
            + project.tracks[0].events[1].duration_beats,
            project.sections[0].end_beat,
        )
        self.assertEqual(project.phrases[1].variation_of, "p1")
        self.assertEqual(project.tracks[0].events[1].articulation, "accent")

    def test_arrangement_plan_continues_and_finalizes_shared_project(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        payload = {
            "schema_version": "1.0",
            "brief": {"style": "arrangement", "duration_seconds": 1},
            "rationale": "Add one answering tone and finalize.",
            "tool_calls": [
                {
                    "name": "add_note",
                    "arguments": {
                        "track_id": "tone",
                        "start_beat": 1,
                        "duration_beats": 1,
                        "pitch": 67,
                        "velocity": 0.5,
                        "section_id": None,
                        "phrase_id": None,
                        "articulation": "normal",
                    },
                },
                {"name": "finalize_project", "arguments": {}},
            ],
        }
        arranged = MusicToolRuntime(project).apply_plan(
            parse_model_plan(
                json.dumps(payload),
                allowed_tools=ARRANGEMENT_TOOL_NAMES,
            ),
            require_finalize=True,
        )
        self.assertEqual(len(arranged.tracks[0].events), 2)

    def test_monophonic_score_overlap_is_rejected(self) -> None:
        payload = _complete_score_plan()
        payload["tool_calls"].append(
            {
                "name": "add_note",
                "arguments": {
                    "track_id": "lead",
                    "start_beat": 0.5,
                    "duration_beats": 0.5,
                    "pitch": 67,
                    "velocity": 0.5,
                    "section_id": "a",
                    "phrase_id": "p1",
                    "articulation": "normal",
                },
            }
        )
        project = MusicToolRuntime().apply_plan(parse_model_plan(json.dumps(payload)))
        with self.assertRaisesRegex(DomainValidationError, "monophonic"):
            project.validate()

    def test_chord_note_duration_cannot_exceed_spacing(self) -> None:
        payload = _short_plan()
        payload["tool_calls"].append(
            {
                "name": "add_chord_sequence",
                "arguments": {
                    "track_id": "tone",
                    "start_beat": 0,
                    "beats_per_chord": 0.5,
                    "note_duration_beats": 1,
                    "chords": [[60, 64, 67]],
                    "velocity": 0.5,
                    "section_id": None,
                    "phrase_id": None,
                    "articulation": "normal",
                },
            }
        )
        with self.assertRaisesRegex(DomainValidationError, "cannot exceed"):
            MusicToolRuntime().apply_plan(parse_model_plan(json.dumps(payload)))

    def test_unknown_tool_is_rejected(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][0]["name"] = "run_shell"
        with self.assertRaises(DomainValidationError):
            parse_model_plan(json.dumps(payload))

    def test_acceptance_fixture_is_generic_tool_plan(self) -> None:
        raw = (ROOT / "tests" / "fixtures" / "generic_plan.json").read_text(
            encoding="utf-8"
        )
        plan = parse_model_plan(raw)
        project = MusicToolRuntime().apply_plan(plan)
        project.validate(forbid_vocals=True)
        self.assertEqual(project.duration_seconds, 1)
        self.assertEqual(len(project.tracks), 1)
        self.assertEqual(sum(len(track.events) for track in project.tracks), 1)

    def test_unknown_instrument_is_rejected(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["instrument"]["id"] = "magic_jazz"
        plan = parse_model_plan(json.dumps(payload))
        project = MusicToolRuntime().apply_plan(plan)
        with self.assertRaises(DomainValidationError):
            project.validate()

    def test_midi_assigns_general_midi_program_from_instrument(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["instrument"]["id"] = "upright_bass"
        plan = parse_model_plan(json.dumps(payload))
        project = MusicToolRuntime().apply_plan(plan)
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "program.mid"
            write_midi(project, midi_path)
            self.assertIn(bytes([0xC0, 32]), midi_path.read_bytes())

    def test_midi_assigns_distinct_channels_to_pitched_tracks(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["instrument"]["id"] = "violin"
        payload["tool_calls"].extend(
            [
                {
                    "name": "add_track",
                    "arguments": {
                        "id": "flute",
                        "name": "Flute",
                        "role": "counterline",
                        "instrument": {"id": "flute"},
                    },
                },
                {
                    "name": "add_note",
                    "arguments": {
                        "track_id": "flute",
                        "start_beat": 0,
                        "duration_beats": 1,
                        "pitch": 72,
                        "velocity": 0.6,
                        "section_id": None,
                        "phrase_id": None,
                        "articulation": "normal",
                    },
                },
            ]
        )
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(payload))
        )
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "channels.mid"
            write_midi(project, midi_path)
            raw = midi_path.read_bytes()
        self.assertIn(bytes([0xC0, 40]), raw)
        self.assertIn(bytes([0xC1, 73]), raw)

    def test_tenor_sax_is_supported_in_score_and_midi(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["instrument"]["id"] = "tenor_sax"
        plan = parse_model_plan(json.dumps(payload))
        project = MusicToolRuntime().apply_plan(plan)
        project.validate()

        pitched_ids = {
            item.id for item in INSTRUMENT_CATALOG if not item.is_percussion
        }
        self.assertEqual(set(GM_PROGRAM_BY_INSTRUMENT), pitched_ids)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            midi_path = root / "tenor-sax.mid"
            write_midi(project, midi_path)
            self.assertIn(bytes([0xC0, 66]), midi_path.read_bytes())

    def test_catalog_covers_orchestral_families_and_percussion(self) -> None:
        self.assertGreaterEqual(len(SUPPORTED_INSTRUMENT_IDS), 50)
        expected = {
            "violin",
            "cello",
            "flute",
            "oboe",
            "clarinet",
            "bassoon",
            "trumpet",
            "french_horn",
            "trombone",
            "tuba",
            "timpani",
            "snare_drum",
        }
        self.assertTrue(expected.issubset(SUPPORTED_INSTRUMENT_IDS))

    def test_percussion_instrument_owns_channel_and_drum_note(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["instrument"]["id"] = "snare_drum"
        payload["tool_calls"][2]["arguments"]["pitch"] = 60
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(payload))
        )
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "snare.mid"
            write_midi(project, midi_path)
            raw = midi_path.read_bytes()
        snare = instrument_definition("snare_drum")
        self.assertIn(bytes([0x99, snare.percussion_note, 89]), raw)
        self.assertNotIn(bytes([0x99, 60, 89]), raw)

    def test_openai_schema_is_strict_at_the_root(self) -> None:
        schema = music_plan_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        tool_schemas = schema["properties"]["tool_calls"]["items"]["anyOf"]
        add_track = tool_schemas[4]
        instrument = add_track["properties"]["arguments"]["properties"]["instrument"]
        self.assertIn("id", instrument["required"])
        self.assertIn("tenor_sax", instrument["properties"]["id"]["enum"])
        add_note = tool_schemas[5]["properties"]["arguments"]["properties"]
        self.assertEqual(add_note["velocity"]["minimum"], 0.0)
        self.assertEqual(add_note["velocity"]["maximum"], 1.0)
        add_pattern = tool_schemas[6]["properties"]["arguments"]["properties"]
        self.assertEqual(add_pattern["velocities"]["items"]["minimum"], 0.0)
        self.assertEqual(add_pattern["velocities"]["items"]["maximum"], 1.0)
        add_chords = tool_schemas[7]["properties"]["arguments"]["properties"]
        self.assertEqual(add_chords["velocity"]["minimum"], 0.0)
        self.assertEqual(add_chords["velocity"]["maximum"], 1.0)

    @patch("continuo.composition.provider.urllib.request.urlopen")
    def test_openai_provider_extracts_structured_output(self, urlopen) -> None:
        plan_text = json.dumps(_short_plan())
        envelope = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": plan_text}],
                }
            ],
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(envelope).encode("utf-8")

        urlopen.return_value = Response()
        provider = OpenAIResponsesProvider(api_key="test-key", model="test-model")
        skill_manifest = tool_manifest(CORE_STAGE)
        skill_manifest.update(
            {
                "active_skills": [
                    {"id": "test-skill", "version": "1.0", "description": "test"}
                ],
                "skill_instructions": "Use deliberate modal voice leading.",
            }
        )
        raw = provider.generate("test prompt", skill_manifest)
        self.assertEqual(parse_model_plan(raw).schema_version, "1.0")
        self.assertEqual(provider.audit_record(), envelope)
        generate_payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("test-skill", generate_payload["instructions"])
        self.assertIn("modal voice leading", generate_payload["instructions"])
        self.assertIn("host_rules", json.loads(generate_payload["input"]))
        repaired = provider.repair(
            prompt="test prompt",
            previous_response=raw,
            validation_error="velocity must be between 0.0 and 1.0; got 42.0",
            tool_manifest=skill_manifest,
        )
        self.assertEqual(parse_model_plan(repaired).schema_version, "1.0")
        repair_payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("complete corrected replacement for this stage", repair_payload["input"])
        self.assertIn("make the smallest change", repair_payload["input"])
        self.assertIn("audit every tool call", repair_payload["input"])
        self.assertIn("got 42.0", repair_payload["input"])

    @patch("continuo.composition.provider.time.sleep")
    @patch("continuo.composition.provider.urllib.request.urlopen")
    def test_openai_provider_retries_transient_network_errors(self, urlopen, sleep) -> None:
        plan_text = json.dumps(_short_plan())
        envelope = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": plan_text}],
                }
            ],
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(envelope).encode("utf-8")

        urlopen.side_effect = [urllib.error.URLError("temporary"), Response()]
        provider = OpenAIResponsesProvider(
            api_key="test-key",
            model="test-model",
            max_request_attempts=2,
        )
        raw = provider.generate("test prompt", tool_manifest(CORE_STAGE))
        self.assertEqual(parse_model_plan(raw).schema_version, "1.0")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("continuo.composition.provider.urllib.request.urlopen")
    def test_openai_provider_arranges_with_available_instrument_catalog(self, urlopen) -> None:
        def envelope(text: str) -> dict:
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
            }

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        arrangement = {
            "schema_version": "1.0",
            "brief": {"style": "arrangement", "duration_seconds": 1},
            "rationale": "The recorded core is already complete.",
            "tool_calls": [{"name": "finalize_project", "arguments": {}}],
        }
        urlopen.return_value = Response(envelope(json.dumps(arrangement)))
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        provider = OpenAIResponsesProvider(api_key="test-key", model="test-model")
        manifest = tool_manifest(ARRANGEMENT_STAGE, current_project=project)
        manifest.update(
            {
                "active_skills": [],
                "skill_instructions": "Arrange against the existing phrase.",
            }
        )
        raw_arrangement = provider.generate(
            "Create a warm texture",
            manifest,
        )
        parsed = parse_model_plan(
            raw_arrangement,
            allowed_tools=ARRANGEMENT_TOOL_NAMES,
        )
        self.assertEqual(parsed.tool_calls[-1].name, "finalize_project")
        arrangement_payload = json.loads(
            urlopen.call_args_list[0].args[0].data.decode("utf-8")
        )
        self.assertEqual(
            arrangement_payload["text"]["format"]["name"],
            "continuo_arrangement_plan",
        )
        composition_input = json.loads(arrangement_payload["input"])
        self.assertIn("available_instruments", composition_input)
        self.assertIn(
            "synth_pad_warm",
            [item["id"] for item in composition_input["available_instruments"]],
        )
        self.assertIn(
            "arranging composer",
            arrangement_payload["instructions"],
        )


class RuntimeTests(unittest.TestCase):
    def test_cross_section_phrase_policy_rejects_section_aligned_phrasing(self) -> None:
        payload = _complete_score_plan()
        for call in payload["tool_calls"]:
            arguments = call["arguments"]
            if call["name"] == "add_phrase" and arguments["id"] == "p1":
                arguments["end_beat"] = 2
            elif call["name"] == "add_phrase" and arguments["id"] == "p2":
                arguments["start_beat"] = 2
            elif (
                call["name"] == "add_note"
                and arguments["track_id"] == "lead"
                and arguments["start_beat"] == 1
            ):
                arguments["duration_beats"] = 1
            elif (
                call["name"] == "add_note"
                and arguments["track_id"] == "lead"
                and arguments["start_beat"] == 2.5
            ):
                arguments["phrase_id"] = "p2"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                DomainValidationError,
                "requires at least one musical phrase",
            ):
                AgentRuntime(renderer=_TestSoundFontRenderer()).run(
                    prompt="Create a connected four-second phrase",
                    provider=RecordedProvider(response),
                    output_dir=root / "output",
                    policy=RunPolicy(
                        expected_duration_seconds=4,
                        require_cross_section_phrase=True,
                    ),
                )

    def test_unavailable_required_instrument_fails_before_composition(self) -> None:
        class TrackingProvider:
            provider_name = "test-live"
            model_name = "test-model"

            def __init__(self):
                self.called = False

            def generate(self, prompt, tool_manifest):
                del prompt, tool_manifest
                self.called = True
                return json.dumps(_short_plan())

        provider = TrackingProvider()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with self.assertRaisesRegex(DomainValidationError, "required instruments"):
                AgentRuntime(renderer=_TestSoundFontRenderer()).run(
                    prompt="Create a warm one-second texture",
                    provider=provider,
                    output_dir=output,
                    policy=RunPolicy(
                        expected_duration_seconds=1,
                        required_instrument_ids=("acoustic_grand_piano",),
                    ),
                )
        self.assertFalse(provider.called)

    def test_soundfont_runtime_maps_the_frozen_score_deterministically(self) -> None:
        class CompositionProvider:
            provider_name = "test-live"
            model_name = "test-model"

            def __init__(self):
                self.manifests: list[dict] = []

            def generate(self, prompt, manifest):
                del prompt
                self.manifests.append(manifest)
                if manifest["composition_stage"] == ARRANGEMENT_STAGE:
                    return json.dumps(_arrangement_plan())
                return json.dumps(_short_plan())

        class MappingRenderer:
            name = "fluidsynth-soundfont"

            def soundfont_profile(self):
                return _test_soundfont_profile()

            def render(self, project, output_path, soundfont_mapping=None):
                self.soundfont_mapping = soundfont_mapping
                return _TestSoundFontRenderer().render(
                    project,
                    output_path,
                    soundfont_mapping,
                )

        provider = CompositionProvider()
        renderer = MappingRenderer()
        expected_project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        expected_project = MusicToolRuntime(expected_project).apply_plan(
            parse_model_plan(
                json.dumps(_arrangement_plan()),
                allowed_tools=ARRANGEMENT_TOOL_NAMES,
            ),
            require_finalize=True,
        )
        expected_project.fit_duration_to_score()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            report = AgentRuntime(renderer=renderer).run(
                prompt="Create a warm one-second texture",
                provider=provider,
                output_dir=output,
                policy=RunPolicy(expected_duration_seconds=1),
            )
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            mapping_ir = json.loads(
                (output / "soundfont_mapping_ir.json").read_text(encoding="utf-8")
            )
            midi = (output / "composition.mid").read_bytes()
            mapping_responses = list(output.glob("soundfont_mapping_response*"))
            has_performance_ir = (output / "expressive_performance_ir.json").exists()

        self.assertEqual(
            report["planning"]["soundfont_mapping"]["source"],
            "deterministic-profile",
        )
        self.assertIsNone(report["planning"]["soundfont_mapping"]["model"])
        self.assertEqual(run["soundfont_mapping_attempts"], 0)
        self.assertFalse(has_performance_ir)
        states = [item["state"] for item in run["events"]]
        self.assertLess(states.index("SOUNDFONT_PROFILED"), states.index("CORE_MODELLED"))
        self.assertLess(states.index("SCORE_FROZEN"), states.index("SOUNDFONT_MAPPED"))
        self.assertEqual(mapping_ir["tracks"][0]["preset"]["id"], "000-089")
        self.assertEqual(
            mapping_ir["score_sha256"],
            score_sha256(expected_project),
        )
        self.assertEqual(mapping_responses, [])
        self.assertIsNotNone(renderer.soundfont_mapping)
        self.assertIn(bytes([0xB0, 0, 0]), midi)
        for manifest in provider.manifests:
            ids = {item["id"] for item in manifest["available_instruments"]}
            self.assertIn("synth_pad_warm", ids)
            self.assertNotIn("acoustic_grand_piano", ids)

    def test_end_to_end_with_recorded_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_text(json.dumps(_short_plan()), encoding="utf-8")
            output = root / "output"
            report = AgentRuntime(renderer=_TestSoundFontRenderer()).run(
                prompt="Create one second of abstract electronic sound",
                provider=RecordedProvider(response),
                output_dir=output,
                policy=RunPolicy(expected_duration_seconds=1),
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["provider"]["name"], "recorded")
            self.assertTrue((output / "audio.wav").exists())
            self.assertTrue((output / "composition.mid").exists())
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["state"], "VERIFIED")
            self.assertEqual(run["core_attempts"], 1)
            self.assertEqual(run["arrangement_attempts"], 1)
            self.assertEqual(
                [skill["id"] for skill in run["skills"]],
                ["conservatory-composition"],
            )
            self.assertEqual(
                [skill["id"] for skill in report["planning"]["skills"]],
                ["conservatory-composition"],
            )

    def test_score_end_sets_duration_and_target_is_advisory(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][2]["arguments"]["duration_beats"] = 3
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_text(json.dumps(payload), encoding="utf-8")
            output = root / "output"

            report = AgentRuntime(renderer=_TestSoundFontRenderer()).run(
                prompt="Create about one second of abstract electronic sound",
                provider=RecordedProvider(response),
                output_dir=output,
                policy=RunPolicy(
                    expected_duration_seconds=1,
                    duration_tolerance_seconds=0.1,
                ),
            )

            adjustment = json.loads(
                (output / "core_duration.attempt-01.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(report["status"], "passed")
        self.assertAlmostEqual(report["project"]["duration_seconds"], 1.5)
        self.assertFalse(report["duration_target"]["within_target_tolerance"])
        self.assertEqual(report["duration_target"]["acceptance"], "advisory")
        self.assertAlmostEqual(adjustment["proposed_duration_seconds"], 1)
        self.assertAlmostEqual(adjustment["score_duration_seconds"], 1.5)

    def test_baritone_sax_case_passes_the_full_recorded_pipeline(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "baritone_sax_legato_8bars.case.json"
        )
        fixture = (
            ROOT / "tests" / "fixtures" / "baritone_sax_legato_8bars.plan.json"
        )
        policy = RunPolicy(
            expected_duration_seconds=case.expected_duration_seconds,
            forbid_vocals=case.forbid_vocals,
            duration_tolerance_seconds=case.duration_tolerance_seconds,
            require_cross_section_phrase=case.require_cross_section_phrase,
            expected_track_count=case.expected_track_count,
            required_instrument_ids=case.required_instrument_ids,
            minimum_slur_connections=case.minimum_slur_connections,
            minimum_breath_connections=case.minimum_breath_connections,
            required_automation_parameters=case.required_automation_parameters,
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            report = AgentRuntime(renderer=_TestSoundFontRenderer()).run(
                prompt=case.prompt,
                provider=RecordedProvider(fixture),
                output_dir=output,
                policy=policy,
                case_id=case.id,
            )
            midi = (output / "composition.mid").read_bytes()

        self.assertEqual(report["status"], "passed")
        self.assertGreaterEqual(report["performance"]["slur_connections"], 6)
        self.assertGreaterEqual(report["performance"]["breath_connections"], 1)
        self.assertEqual(
            report["render"]["performance_realization"]["slur"],
            "MIDI note-overlap fallback; transition samples are not declared",
        )
        self.assertIn(bytes([0xB0, 11]), midi)
        self.assertIn(bytes([0xE0]), midi)

    def test_baritone_sax_case_rejects_a_detached_line(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "baritone_sax_legato_8bars.case.json"
        )
        payload = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "baritone_sax_legato_8bars.plan.json"
            ).read_text(encoding="utf-8")
        )
        sequence = next(
            call for call in payload["tool_calls"] if call["name"] == "add_note_sequence"
        )
        for note in sequence["arguments"]["notes"]:
            if note["connection_to_next"] == "slur":
                note["connection_to_next"] = "separate"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "detached.json"
            response.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                DomainValidationError,
                "requires at least 6 slur connections",
            ):
                AgentRuntime(renderer=_TestSoundFontRenderer()).run(
                    prompt=case.prompt,
                    provider=RecordedProvider(response),
                    output_dir=root / "output",
                    policy=RunPolicy(
                        expected_duration_seconds=case.expected_duration_seconds,
                        forbid_vocals=case.forbid_vocals,
                        duration_tolerance_seconds=case.duration_tolerance_seconds,
                        expected_track_count=case.expected_track_count,
                        required_instrument_ids=case.required_instrument_ids,
                        minimum_slur_connections=case.minimum_slur_connections,
                        minimum_breath_connections=case.minimum_breath_connections,
                        required_automation_parameters=(
                            case.required_automation_parameters
                        ),
                    ),
                )

    def test_live_provider_repairs_invalid_plan_inside_one_run(self) -> None:
        invalid = _short_plan()
        invalid["tool_calls"][2]["arguments"]["velocity"] = 42
        valid = _short_plan()

        class RepairingProvider:
            provider_name = "test-live"
            model_name = "test-model"

            def __init__(self) -> None:
                self.repairs: list[dict] = []
                self.manifests: list[dict] = []

            def generate(self, prompt, tool_manifest):
                del prompt
                self.manifests.append(tool_manifest)
                if tool_manifest["composition_stage"] == ARRANGEMENT_STAGE:
                    return json.dumps(_arrangement_plan())
                return json.dumps(invalid)

            def repair(self, **kwargs):
                self.repairs.append(kwargs)
                return json.dumps(valid)

        provider = RepairingProvider()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            report = AgentRuntime(
                renderer=_TestSoundFontRenderer(), max_plan_attempts=2
            ).run(
                prompt="Create one second of abstract electronic sound",
                provider=provider,
                output_dir=output,
                policy=RunPolicy(expected_duration_seconds=1),
            )

            self.assertEqual(report["status"], "passed")
            self.assertEqual(
                report["planning"]["composition"]["core"]["attempts"],
                2,
            )
            self.assertTrue(
                report["planning"]["composition"]["core"]["repaired"]
            )
            self.assertEqual(
                [skill["id"] for skill in report["planning"]["skills"]],
                ["conservatory-composition"],
            )
            self.assertEqual(len(provider.repairs), 1)
            self.assertEqual(
                [skill["id"] for skill in provider.manifests[0]["active_skills"]],
                ["conservatory-composition"],
            )
            self.assertIn("got 42", provider.repairs[0]["validation_error"])
            self.assertTrue((output / "core_response.attempt-01.raw.json").exists())
            self.assertTrue((output / "core_response.attempt-02.raw.json").exists())
            self.assertTrue((output / "core_error.attempt-01.json").exists())
            final_response = json.loads(
                (output / "core_response.raw.json").read_text(encoding="utf-8")
            )
            self.assertEqual(final_response["tool_calls"][2]["arguments"]["velocity"], 0.7)
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["state"], "VERIFIED")
            self.assertEqual(run["core_attempts"], 2)
            self.assertEqual(run["arrangement_attempts"], 1)
            self.assertIn(
                "CORE_REJECTED",
                [event["state"] for event in run["events"]],
            )

    def test_recorded_provider_does_not_repair_invalid_plan(self) -> None:
        invalid = _short_plan()
        invalid["tool_calls"][2]["arguments"]["velocity"] = 42
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_text(json.dumps(invalid), encoding="utf-8")
            output = root / "output"
            with self.assertRaises(DomainValidationError):
                AgentRuntime(
                    renderer=_TestSoundFontRenderer(), max_plan_attempts=3
                ).run(
                    prompt="Create one second of abstract electronic sound",
                    provider=RecordedProvider(response),
                    output_dir=output,
                    policy=RunPolicy(expected_duration_seconds=1),
                )
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["state"], "FAILED")
            self.assertEqual(run["core_attempts"], 1)
            self.assertTrue((output / "core_error.attempt-01.json").exists())


class CaseTests(unittest.TestCase):
    def test_bolero_case_pins_the_orchestral_arrangement_regression(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "bolero_electronic_excerpt.case.json"
        )
        self.assertEqual(case.expected_duration_seconds, 95)
        self.assertEqual(case.expected_track_count, 5)
        self.assertEqual(
            set(case.required_instrument_ids),
            {
                "flute",
                "clarinet",
                "viola",
                "cello",
                "snare_drum",
            },
        )
        self.assertEqual(case.minimum_slur_connections, 190)
        self.assertEqual(case.minimum_breath_connections, 2)
        self.assertEqual(
            case.required_automation_parameters,
            ("expression", "modulation"),
        )

    def test_georgia_case_pins_the_jazz_reference_regression(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "georgia_on_my_mind_reference.case.json"
        )
        self.assertEqual(case.expected_duration_seconds, 100)
        self.assertEqual(case.expected_track_count, 2)
        self.assertEqual(
            set(case.required_instrument_ids),
            {"baritone_sax", "acoustic_grand_piano"},
        )
        self.assertEqual(case.minimum_slur_connections, 80)
        self.assertEqual(case.minimum_breath_connections, 3)
        self.assertEqual(case.required_automation_parameters, ("expression",))

    def test_baritone_sax_case_pins_performance_requirements(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "baritone_sax_legato_8bars.case.json"
        )
        self.assertEqual(case.expected_track_count, 1)
        self.assertEqual(case.required_instrument_ids, ("baritone_sax",))
        self.assertEqual(case.minimum_slur_connections, 6)
        self.assertEqual(case.minimum_breath_connections, 1)
        self.assertEqual(
            case.required_automation_parameters,
            ("expression", "breath", "pitch_bend"),
        )

    def test_epic_case_allows_five_seconds_of_duration_tolerance(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "epic_cinematic_symphony_180s.case.json"
        )
        self.assertEqual(case.expected_duration_seconds, 180)
        self.assertEqual(case.duration_tolerance_seconds, 5.0)

    def test_leisure_jazz_case_requires_cross_section_phrase(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "leisure_jazz_90s.case.json"
        )
        self.assertTrue(case.require_cross_section_phrase)

    def test_case_can_pin_the_default_openai_model(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "epic_cinematic_symphony_180s.case.json"
        )
        self.assertEqual(case.model, "gpt-5.6-luna")
        self.assertEqual(case.snapshot()["model"], "gpt-5.6-luna")

    @patch("continuo.cli.AgentRuntime.run", return_value={"status": "passed"})
    @patch("continuo.cli.OpenAIResponsesProvider")
    def test_cli_uses_case_model_before_environment_default(
        self, provider_class, _run
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory) / "artifacts"
            output = StringIO()
            with patch.dict(
                "os.environ",
                {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "environment-model"},
            ), patch(
                "continuo.cli.FluidSynthRenderer",
                return_value=_TestSoundFontRenderer(),
            ), redirect_stdout(output):
                exit_code = main(
                    [
                        "generate",
                        "--case",
                        str(
                            ROOT
                            / "eval"
                            / "cases"
                            / "epic_cinematic_symphony_180s.case.json"
                        ),
                        "--artifacts-root",
                        str(artifacts),
                        "--provider",
                        "openai",
                    ]
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(provider_class.call_args.kwargs["model"], "gpt-5.6-luna")

    def test_case_run_is_grouped_under_case_id(self) -> None:
        case = ResearchCase.load(ROOT / "eval" / "cases" / "cafe_jazz_60s.case.json")
        with tempfile.TemporaryDirectory() as directory:
            workspace = ArtifactStore(Path(directory)).create_run(case)
            self.assertEqual(workspace.case_id, "cafe_jazz_60s")
            self.assertEqual(
                workspace.path.parent.parent,
                Path(directory) / "cafe_jazz_60s",
            )
            snapshot = json.loads(
                (workspace.path / "case.json").read_text(encoding="utf-8")
            )
            self.assertEqual(snapshot["id"], "cafe_jazz_60s")

    def test_case_id_cannot_escape_artifact_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.case.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "../escape",
                        "prompt": "test",
                        "policy": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CaseValidationError):
                ResearchCase.load(path)

    def test_cli_case_mode_creates_a_managed_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            response = root / "response.json"
            response.write_text(json.dumps(_short_plan()), encoding="utf-8")
            case_path = root / "test.case.json"
            case_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "id": "managed_case_test",
                        "prompt": "Create one second of abstract electronic sound",
                        "recorded_response": response.name,
                        "policy": {
                            "expected_duration_seconds": 1,
                            "forbid_vocals": True,
                            "duration_tolerance_seconds": 0.1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with patch(
                "continuo.cli.FluidSynthRenderer",
                return_value=_TestSoundFontRenderer(),
            ), redirect_stdout(output):
                exit_code = main(
                    [
                        "generate",
                        "--case",
                        str(case_path),
                        "--artifacts-root",
                        str(artifacts),
                        "--provider",
                        "recorded",
                    ]
                )
            self.assertEqual(exit_code, 0)
            result = json.loads(output.getvalue())
            run_path = Path(result["artifact_dir"])
            self.assertEqual(run_path.parent.parent, artifacts / "managed_case_test")
            self.assertEqual(result["run"]["case_id"], "managed_case_test")
            self.assertTrue((run_path / "audio.wav").exists())


if __name__ == "__main__":
    unittest.main()
