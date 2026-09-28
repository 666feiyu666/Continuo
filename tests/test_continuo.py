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
from continuo.instruments import (
    INSTRUMENT_CATALOG,
    SUPPORTED_INSTRUMENT_IDS,
    instrument_definition,
)
from continuo.midi import GM_PROGRAM_BY_INSTRUMENT, write_midi
from continuo.openai_provider import (
    OpenAIResponsesProvider,
    music_plan_schema,
    soundfont_mapping_schema,
)
from continuo.planning import RecordedProvider, parse_model_plan
from continuo.rendering import ReferenceWavRenderer
from continuo.runtime import AgentRuntime, RunPolicy
from continuo.skills import SkillRegistry
from continuo.soundfont_mapping import (
    apply_soundfont_mapping,
    parse_soundfont_mapping,
)
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

    def test_model_selected_preset_is_validated_and_compiled_to_midi(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        mapping = parse_soundfont_mapping(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "assignments": [
                        {
                            "track_id": "tone",
                            "preset_id": "011-089",
                            "reason": "The alternate pad better fits the role.",
                        }
                    ],
                }
            )
        )
        apply_soundfont_mapping(project, mapping, _test_soundfont_profile())
        binding = project.tracks[0].instrument.soundfont_preset
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual((binding.bank, binding.program), (11, 89))
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "model-mapped.mid"
            write_midi(project, midi_path)
            raw = midi_path.read_bytes()
        self.assertIn(bytes([0xB0, 0, 11]), raw)
        self.assertIn(bytes([0xC0, 89]), raw)

    def test_soundfont_mapping_rejects_percussion_kit_for_pitched_track(self) -> None:
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        mapping = parse_soundfont_mapping(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "assignments": [
                        {
                            "track_id": "tone",
                            "preset_id": "128-040",
                            "reason": "invalid test proposal",
                        }
                    ],
                }
            )
        )
        with self.assertRaises(DomainValidationError):
            apply_soundfont_mapping(project, mapping, _test_soundfont_profile())

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
            ["conservatory-composition", "soundfont-performance"],
        )
        self.assertIn("violin (recommended MIDI range", soundfont_selection.instructions)
        self.assertIn("snare_drum (fixed drum note 38)", soundfont_selection.instructions)

    def test_generated_notes_are_fitted_to_the_project_timeline(self) -> None:
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
                },
            }
        )

        project = MusicToolRuntime().apply_plan(parse_model_plan(json.dumps(payload)))

        self.assertEqual(len(project.tracks[0].events), 1)
        self.assertAlmostEqual(project.tracks[0].events[0].duration_beats, 0.25)
        project.validate()

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
        add_track = tool_schemas[2]
        instrument = add_track["properties"]["arguments"]["properties"]["instrument"]
        self.assertIn("id", instrument["required"])
        self.assertIn("tenor_sax", instrument["properties"]["id"]["enum"])
        add_note = tool_schemas[3]["properties"]["arguments"]["properties"]
        self.assertEqual(add_note["velocity"]["minimum"], 0.0)
        self.assertEqual(add_note["velocity"]["maximum"], 1.0)
        add_pattern = tool_schemas[4]["properties"]["arguments"]["properties"]
        self.assertEqual(add_pattern["velocities"]["items"]["minimum"], 0.0)
        self.assertEqual(add_pattern["velocities"]["items"]["maximum"], 1.0)
        add_chords = tool_schemas[5]["properties"]["arguments"]["properties"]
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
    def test_openai_provider_runs_a_separate_soundfont_mapping_call(self, urlopen) -> None:
        mapping_text = json.dumps(
            {
                "schema_version": "1.0",
                "assignments": [
                    {
                        "track_id": "tone",
                        "preset_id": "011-089",
                        "reason": "A softer alternate pad suits the texture role.",
                    }
                ],
            }
        )
        envelope = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": mapping_text}],
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
        project = MusicToolRuntime().apply_plan(
            parse_model_plan(json.dumps(_short_plan()))
        )
        provider = OpenAIResponsesProvider(api_key="test-key", model="test-model")
        raw = provider.map_soundfont(
            prompt="Create a warm texture",
            project=project,
            soundfont_profile=_test_soundfont_profile(),
            skill_instructions="Use the inspected SoundFont inventory.",
        )
        self.assertEqual(parse_soundfont_mapping(raw).assignments[0].preset_id, "011-089")
        request_payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(
            request_payload["text"]["format"]["name"],
            "continuo_soundfont_mapping",
        )
        self.assertEqual(request_payload["model"], "test-model")
        self.assertIn("test.sf2", request_payload["input"])
        self.assertIn("SoundFont performance specialist", request_payload["instructions"])


class RuntimeTests(unittest.TestCase):
    def test_soundfont_runtime_maps_presets_before_midi_compilation(self) -> None:
        class MappingProvider:
            provider_name = "test-live"
            model_name = "test-model"

            def generate(self, prompt, tool_manifest):
                del prompt, tool_manifest
                return json.dumps(_short_plan())

            def map_soundfont(self, **kwargs):
                self.mapping_kwargs = kwargs
                return json.dumps(
                    {
                        "schema_version": "1.0",
                        "assignments": [
                            {
                                "track_id": "tone",
                                "preset_id": "011-089",
                                "reason": "Model chose a profile-specific pad variant.",
                            }
                        ],
                    }
                )

        class MappingRenderer:
            name = "fluidsynth-soundfont"

            def soundfont_profile(self):
                return _test_soundfont_profile()

            def render(self, project, output_path):
                return ReferenceWavRenderer().render(project, output_path)

        provider = MappingProvider()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            report = AgentRuntime(renderer=MappingRenderer()).run(
                prompt="Create a warm one-second texture",
                provider=provider,
                output_dir=output,
                policy=RunPolicy(expected_duration_seconds=1),
            )
            run = json.loads((output / "run.json").read_text(encoding="utf-8"))
            music_ir = json.loads(
                (output / "music_ir.json").read_text(encoding="utf-8")
            )
            midi = (output / "composition.mid").read_bytes()

        self.assertEqual(report["planning"]["soundfont_mapping"]["source"], "model")
        self.assertEqual(run["soundfont_mapping_attempts"], 1)
        self.assertIn("SOUNDFONT_MAPPED", [item["state"] for item in run["events"]])
        self.assertEqual(
            music_ir["tracks"][0]["instrument"]["soundfont_preset"]["id"],
            "011-089",
        )
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
