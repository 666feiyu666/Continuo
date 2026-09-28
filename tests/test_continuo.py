from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuo.cases import ArtifactStore, CaseValidationError, ResearchCase
from continuo.cli import main
from continuo.domain import DomainValidationError
from continuo.expressive_performance import (
    parse_expressive_performance,
    resolve_expressive_performance,
    validate_expressive_performance,
)
from continuo.instruments import (
    INSTRUMENT_CATALOG,
    SUPPORTED_INSTRUMENT_IDS,
    instrument_definition,
)
from continuo.midi import GM_PROGRAM_BY_INSTRUMENT, write_midi
from continuo.openai_provider import (
    OpenAIResponsesProvider,
    expressive_performance_schema,
    music_plan_schema,
    soundfont_mapping_schema,
)
from continuo.planning import RecordedProvider, parse_model_plan
from continuo.rendering import ReferenceWavRenderer
from continuo.runtime import AgentRuntime, RunPolicy
from continuo.skills import SkillRegistry
from continuo.soundfont_mapping import (
    parse_soundfont_mapping,
    resolve_soundfont_mapping,
    validate_soundfont_mapping,
)
from continuo.score_identity import score_sha256
from continuo.soundfont_profile import (
    SoundFontPreset,
    SoundFontProfile,
    parse_fluidsynth_preset_listing,
)
from continuo.supercollider import SuperColliderNrtRenderer
from continuo.tools import MusicToolRuntime


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


def _expressive_payload() -> dict:
    return {
        "schema_version": "1.0",
        "tracks": [
            {
                "track_id": "tone",
                "base_expression": 96,
                "phrases": [],
                "note_adjustments": [],
            }
        ],
    }


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


def _lyrical_performance_payload() -> dict:
    return {
        "schema_version": "1.0",
        "tracks": [
            {
                "track_id": "lead",
                "base_expression": 82,
                "phrases": [
                    {
                        "phrase_id": "p1",
                        "connection": "legato",
                        "start_expression": 78,
                        "peak_expression": 112,
                        "peak_beat": 2.25,
                        "end_expression": 94,
                        "breath_after_beats": 0.0,
                    },
                    {
                        "phrase_id": "p2",
                        "connection": "connected",
                        "start_expression": 94,
                        "peak_expression": 102,
                        "peak_beat": 3.4,
                        "end_expression": 72,
                        "breath_after_beats": 0.0,
                    },
                ],
                "note_adjustments": [
                    {
                        "note_index": 1,
                        "onset_offset_beats": -0.02,
                        "duration_scale": 1.0,
                        "velocity_scale": 1.08,
                    }
                ],
            }
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

    def test_expressive_performance_and_mapping_compile_to_midi(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        score_before = project.to_dict()
        performance = resolve_expressive_performance(
            project,
            parse_expressive_performance(json.dumps(_expressive_payload())),
        )
        mapping = resolve_soundfont_mapping(
            project,
            parse_soundfont_mapping(json.dumps(_mapping_payload())),
            _test_soundfont_profile(),
        )
        self.assertEqual(project.to_dict(), score_before)
        self.assertEqual(performance.score_sha256, score_sha256(project))
        self.assertEqual(
            (mapping.tracks[0].preset.bank, mapping.tracks[0].preset.program),
            (11, 89),
        )
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "model-mapped.mid"
            write_midi(project, midi_path, performance, mapping)
            raw = midi_path.read_bytes()
        self.assertIn(bytes([0xB0, 0, 11]), raw)
        self.assertIn(bytes([0xC0, 89]), raw)
        self.assertIn(bytes([0xB0, 11, 96]), raw)

    def test_downstream_irs_are_rejected_after_score_mutation(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        performance = resolve_expressive_performance(
            project,
            parse_expressive_performance(json.dumps(_expressive_payload())),
        )
        mapping = resolve_soundfont_mapping(
            project,
            parse_soundfont_mapping(json.dumps(_mapping_payload())),
            _test_soundfont_profile(),
        )
        project.tracks[0].events[0].pitch = 61
        with self.assertRaisesRegex(DomainValidationError, "does not match"):
            validate_expressive_performance(project, performance)
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

    def test_soundfont_mapping_schema_is_limited_to_real_tracks_and_presets(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        schema = soundfont_mapping_schema(project, _test_soundfont_profile())
        assignment = schema["properties"]["assignments"]["items"]
        properties = assignment["properties"]
        self.assertEqual(properties["track_id"]["enum"], ["tone"])
        self.assertEqual(
            properties["preset_id"]["enum"],
            ["000-089", "011-089", "128-040"],
        )

    def test_default_skill_registry_resolves_renderer_skills_deterministically(self) -> None:
        registry = SkillRegistry.default()
        python_ids = [
            item["id"]
            for item in registry.resolve(renderer_name="python-reference").manifest()
        ]
        soundfont_selection = registry.resolve(renderer_name="fluidsynth-soundfont")
        soundfont_ids = [
            item["id"]
            for item in soundfont_selection.manifest()
        ]

        self.assertEqual(python_ids, ["conservatory-composition"])
        self.assertEqual(
            soundfont_ids,
            [
                "conservatory-composition",
                "expressive-performance",
                "soundfont-mapping",
            ],
        )
        self.assertIn("violin (recommended MIDI range", soundfont_selection.instructions)
        self.assertIn("snare_drum (fixed drum note 38)", soundfont_selection.instructions)

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

    def test_cross_section_phrase_compiles_as_one_expression_arc(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_complete_score_plan()))
        )
        performance = resolve_expressive_performance(
            project,
            parse_expressive_performance(
                json.dumps(_lyrical_performance_payload())
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "continuous-phrase.mid"
            write_midi(project, midi_path, performance)
            raw = midi_path.read_bytes()
        self.assertIn(bytes([0xB0, 11, 78]), raw)
        self.assertIn(bytes([0xB0, 11, 112]), raw)
        self.assertIn(bytes([0xB0, 11, 94]), raw)

    def test_expressive_schema_exposes_phrase_and_note_controls(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_complete_score_plan()))
        )
        schema = expressive_performance_schema(project)
        track = schema["properties"]["tracks"]["items"]["properties"]
        phrase = track["phrases"]["items"]["properties"]
        adjustment = track["note_adjustments"]["items"]["properties"]
        self.assertEqual(phrase["phrase_id"]["enum"], ["p1", "p2"])
        self.assertEqual(
            phrase["connection"]["enum"],
            ["legato", "connected", "separated"],
        )
        self.assertEqual(adjustment["onset_offset_beats"]["minimum"], -0.125)

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

    def test_supercollider_compiler_binds_deterministic_event_seeds(self) -> None:
        plan = parse_model_plan(json.dumps(_short_plan()))
        project = MusicToolRuntime().apply_plan(plan)
        renderer = SuperColliderNrtRenderer(executable=Path("sclang"))
        script = renderer.compile_script(project, Path("audio.wav"), Path("score.osc"))
        self.assertIn("RandSeed.ir(1, seed)", script)
        self.assertIn("\\seed,", script)

    def test_supercollider_compiler_uses_acoustic_instrument_profile_and_room_model(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["instrument"]["id"] = (
            "acoustic_grand_piano"
        )
        plan = parse_model_plan(json.dumps(payload))
        project = MusicToolRuntime().apply_plan(plan)
        renderer = SuperColliderNrtRenderer(executable=Path("sclang"))
        script = renderer.compile_script(project, Path("audio.wav"), Path("score.osc"))
        self.assertIn("Ringz.ar", script)
        self.assertIn("FreeVerb2.ar", script)
        self.assertIn("\\roomDelay,", script)
        self.assertIn("\\targetPeak,", script)

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

    def test_tenor_sax_is_supported_across_renderers(self) -> None:
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
            report = ReferenceWavRenderer().render(project, root / "tenor-sax.wav")
            self.assertGreater(report.rms, 0.0005)

        renderer = SuperColliderNrtRenderer(executable=Path("sclang"))
        script = renderer.compile_script(project, Path("audio.wav"), Path("score.osc"))
        self.assertIn("SinOsc.kr(5.2", script)
        self.assertIn("RLPF.ar", script)

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

    @patch("continuo.openai_provider.urllib.request.urlopen")
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
        skill_manifest = {
            "active_skills": [
                {"id": "test-skill", "version": "1.0", "description": "test"}
            ],
            "skill_instructions": "Use deliberate modal voice leading.",
        }
        raw = provider.generate("test prompt", skill_manifest)
        self.assertEqual(parse_model_plan(raw).schema_version, "1.0")
        self.assertEqual(provider.audit_record(), envelope)
        generate_payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("test-skill", generate_payload["instructions"])
        self.assertIn("modal voice leading", generate_payload["instructions"])
        repaired = provider.repair(
            prompt="test prompt",
            previous_response=raw,
            validation_error="velocity must be between 0.0 and 1.0; got 42.0",
            tool_manifest=skill_manifest,
        )
        self.assertEqual(parse_model_plan(repaired).schema_version, "1.0")
        repair_payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("Return a complete corrected replacement plan", repair_payload["input"])
        self.assertIn("got 42.0", repair_payload["input"])

    @patch("continuo.openai_provider.time.sleep")
    @patch("continuo.openai_provider.urllib.request.urlopen")
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
        raw = provider.generate("test prompt", {})
        self.assertEqual(parse_model_plan(raw).schema_version, "1.0")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("continuo.openai_provider.urllib.request.urlopen")
    def test_openai_provider_separates_performance_from_soundfont_mapping(self, urlopen) -> None:
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

        urlopen.side_effect = [
            Response(envelope(json.dumps(_expressive_payload()))),
            Response(envelope(json.dumps(_mapping_payload()))),
        ]
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        provider = OpenAIResponsesProvider(api_key="test-key", model="test-model")
        raw_performance = provider.interpret_performance(
            prompt="Create a warm texture",
            project=project,
            skill_instructions="Shape complete phrases.",
        )
        performance = resolve_expressive_performance(
            project,
            parse_expressive_performance(raw_performance),
        )
        raw_mapping = provider.map_soundfont(
            prompt="Create a warm texture",
            project=project,
            performance=performance,
            soundfont_profile=_test_soundfont_profile(),
            skill_instructions="Use the inspected SoundFont inventory.",
        )
        self.assertEqual(
            parse_soundfont_mapping(raw_mapping).assignments[0].preset_id,
            "011-089",
        )
        performance_payload = json.loads(
            urlopen.call_args_list[0].args[0].data.decode("utf-8")
        )
        mapping_payload = json.loads(
            urlopen.call_args_list[1].args[0].data.decode("utf-8")
        )
        self.assertEqual(
            performance_payload["text"]["format"]["name"],
            "continuo_expressive_performance",
        )
        self.assertEqual(
            mapping_payload["text"]["format"]["name"],
            "continuo_soundfont_mapping",
        )
        self.assertEqual(mapping_payload["model"], "test-model")
        self.assertIn("test.sf2", mapping_payload["input"])
        self.assertIn("expressive_performance_ir", mapping_payload["input"])
        self.assertIn(
            "expressive performance specialist",
            performance_payload["instructions"],
        )


class RuntimeTests(unittest.TestCase):
    def test_performance_provider_cannot_mutate_frozen_score(self) -> None:
        class MutatingProvider:
            provider_name = "test-live"
            model_name = "test-model"

            def generate(self, prompt, tool_manifest):
                del prompt, tool_manifest
                return json.dumps(_short_plan())

            def interpret_performance(self, **kwargs):
                kwargs["project"].tracks[0].events[0].pitch = 61
                return json.dumps(_expressive_payload())

        class SoundFontRenderer:
            name = "fluidsynth-soundfont"

            def soundfont_profile(self):
                return _test_soundfont_profile()

            def render(
                self,
                project,
                output_path,
                performance=None,
                soundfont_mapping=None,
            ):
                return ReferenceWavRenderer().render(
                    project,
                    output_path,
                    performance,
                    soundfont_mapping,
                )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with self.assertRaisesRegex(DomainValidationError, "modified the frozen"):
                AgentRuntime(renderer=SoundFontRenderer()).run(
                    prompt="Create a warm one-second texture",
                    provider=MutatingProvider(),
                    output_dir=output,
                    policy=RunPolicy(expected_duration_seconds=1),
                )
            frozen = json.loads(
                (output / "score_ir.json").read_text(encoding="utf-8")
            )
            self.assertEqual(frozen["tracks"][0]["events"][0]["pitch"], 60)

    def test_soundfont_runtime_freezes_score_before_performance_compilation(self) -> None:
        class PerformanceProvider:
            provider_name = "test-live"
            model_name = "test-model"

            def generate(self, prompt, tool_manifest):
                del prompt, tool_manifest
                return json.dumps(_short_plan())

            def interpret_performance(self, **kwargs):
                self.performance_kwargs = kwargs
                return json.dumps(_expressive_payload())

            def map_soundfont(self, **kwargs):
                self.mapping_kwargs = kwargs
                return json.dumps(_mapping_payload())

        class PerformanceRenderer:
            name = "fluidsynth-soundfont"

            def soundfont_profile(self):
                return _test_soundfont_profile()

            def render(
                self,
                project,
                output_path,
                performance=None,
                soundfont_mapping=None,
            ):
                self.performance = performance
                self.soundfont_mapping = soundfont_mapping
                return ReferenceWavRenderer().render(
                    project,
                    output_path,
                    performance,
                    soundfont_mapping,
                )

        provider = PerformanceProvider()
        renderer = PerformanceRenderer()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            report = AgentRuntime(renderer=renderer).run(
                prompt="Create a warm one-second texture",
                provider=provider,
                output_dir=output,
                policy=RunPolicy(expected_duration_seconds=1),
            )
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            score_ir = json.loads(
                (output / "score_ir.json").read_text(encoding="utf-8")
            )
            expressive_ir = json.loads(
                (output / "expressive_performance_ir.json").read_text(
                    encoding="utf-8"
                )
            )
            mapping_ir = json.loads(
                (output / "soundfont_mapping_ir.json").read_text(encoding="utf-8")
            )
            midi = (output / "composition.mid").read_bytes()

        self.assertEqual(
            report["planning"]["expressive_performance"]["source"],
            "model",
        )
        self.assertEqual(report["planning"]["soundfont_mapping"]["source"], "model")
        self.assertEqual(run["expressive_performance_attempts"], 1)
        self.assertEqual(run["soundfont_mapping_attempts"], 1)
        states = [item["state"] for item in run["events"]]
        self.assertLess(states.index("SCORE_VALIDATED"), states.index("PERFORMANCE_INTERPRETED"))
        self.assertLess(states.index("PERFORMANCE_INTERPRETED"), states.index("SOUNDFONT_MAPPED"))
        self.assertEqual(score_ir["tracks"][0]["instrument"], {"id": "synth_pad_warm"})
        self.assertEqual(expressive_ir["tracks"][0]["base_expression"], 96)
        self.assertEqual(mapping_ir["tracks"][0]["preset"]["id"], "011-089")
        self.assertEqual(expressive_ir["score_sha256"], score_sha256(provider.performance_kwargs["project"]))
        self.assertIsNotNone(renderer.performance)
        self.assertIsNotNone(renderer.soundfont_mapping)
        self.assertIn(bytes([0xB0, 0, 11]), midi)

    def test_end_to_end_with_recorded_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_text(json.dumps(_short_plan()), encoding="utf-8")
            output = root / "output"
            report = AgentRuntime().run(
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
            self.assertEqual(run["plan_attempts"], 1)
            self.assertEqual(
                [skill["id"] for skill in run["skills"]],
                ["conservatory-composition"],
            )
            self.assertEqual(
                [skill["id"] for skill in report["planning"]["skills"]],
                ["conservatory-composition"],
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
                return json.dumps(invalid)

            def repair(self, **kwargs):
                self.repairs.append(kwargs)
                return json.dumps(valid)

        provider = RepairingProvider()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            report = AgentRuntime(max_plan_attempts=2).run(
                prompt="Create one second of abstract electronic sound",
                provider=provider,
                output_dir=output,
                policy=RunPolicy(expected_duration_seconds=1),
            )

            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["planning"]["attempts"], 2)
            self.assertTrue(report["planning"]["repaired"])
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
            self.assertTrue((output / "model_response.attempt-01.raw.json").exists())
            self.assertTrue((output / "model_response.attempt-02.raw.json").exists())
            self.assertTrue((output / "validation_error.attempt-01.json").exists())
            final_response = json.loads(
                (output / "model_response.raw.json").read_text(encoding="utf-8")
            )
            self.assertEqual(final_response["tool_calls"][2]["arguments"]["velocity"], 0.7)
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["state"], "VERIFIED")
            self.assertEqual(run["plan_attempts"], 2)
            self.assertIn("PLAN_REJECTED", [event["state"] for event in run["events"]])

    def test_recorded_provider_does_not_repair_invalid_plan(self) -> None:
        invalid = _short_plan()
        invalid["tool_calls"][2]["arguments"]["velocity"] = 42
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_text(json.dumps(invalid), encoding="utf-8")
            output = root / "output"
            with self.assertRaises(DomainValidationError):
                AgentRuntime(max_plan_attempts=3).run(
                    prompt="Create one second of abstract electronic sound",
                    provider=RecordedProvider(response),
                    output_dir=output,
                    policy=RunPolicy(expected_duration_seconds=1),
                )
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["state"], "FAILED")
            self.assertEqual(run["plan_attempts"], 1)
            self.assertTrue((output / "validation_error.attempt-01.json").exists())


class CaseTests(unittest.TestCase):
    def test_epic_case_allows_five_seconds_of_duration_tolerance(self) -> None:
        case = ResearchCase.load(
            ROOT / "eval" / "cases" / "epic_cinematic_symphony_180s.case.json"
        )
        self.assertEqual(case.expected_duration_seconds, 180)
        self.assertEqual(case.duration_tolerance_seconds, 5.0)

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
                        "--backend",
                        "python",
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
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "generate",
                        "--case",
                        str(case_path),
                        "--artifacts-root",
                        str(artifacts),
                        "--provider",
                        "recorded",
                        "--backend",
                        "python",
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
