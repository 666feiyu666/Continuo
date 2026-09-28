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
from continuo.domain import DomainValidationError, SUPPORTED_SYNTH_VOICES
from continuo.midi import GM_PROGRAM_BY_VOICE, write_midi
from continuo.openai_provider import OpenAIResponsesProvider, music_plan_schema
from continuo.planning import RecordedProvider, parse_model_plan
from continuo.rendering import ReferenceWavRenderer
from continuo.runtime import AgentRuntime, RunPolicy
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
                    "synth": {
                        "voice": "oscillator",
                        "oscillator": "sine",
                        "partials": [1.0],
                        "gain": 0.2,
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


class PlanningTests(unittest.TestCase):
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

    def test_supercollider_compiler_uses_acoustic_voice_and_room_model(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["synth"]["voice"] = "acoustic_piano"
        plan = parse_model_plan(json.dumps(payload))
        project = MusicToolRuntime().apply_plan(plan)
        renderer = SuperColliderNrtRenderer(executable=Path("sclang"))
        script = renderer.compile_script(project, Path("audio.wav"), Path("score.osc"))
        self.assertIn("Ringz.ar", script)
        self.assertIn("FreeVerb2.ar", script)
        self.assertIn("\\roomDelay,", script)
        self.assertIn("\\targetPeak,", script)

    def test_unknown_acoustic_voice_is_rejected(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["synth"]["voice"] = "magic_jazz"
        plan = parse_model_plan(json.dumps(payload))
        project = MusicToolRuntime().apply_plan(plan)
        with self.assertRaises(DomainValidationError):
            project.validate()

    def test_midi_assigns_general_midi_program_from_voice(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["synth"]["voice"] = "upright_bass"
        plan = parse_model_plan(json.dumps(payload))
        project = MusicToolRuntime().apply_plan(plan)
        with tempfile.TemporaryDirectory() as directory:
            midi_path = Path(directory) / "program.mid"
            write_midi(project, midi_path)
            self.assertIn(bytes([0xC0, 32]), midi_path.read_bytes())

    def test_tenor_sax_is_supported_across_renderers(self) -> None:
        payload = _short_plan()
        payload["tool_calls"][1]["arguments"]["synth"]["voice"] = "tenor_sax"
        plan = parse_model_plan(json.dumps(payload))
        project = MusicToolRuntime().apply_plan(plan)
        project.validate()

        self.assertEqual(set(GM_PROGRAM_BY_VOICE), set(SUPPORTED_SYNTH_VOICES))
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

    def test_openai_schema_is_strict_at_the_root(self) -> None:
        schema = music_plan_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        tool_schemas = schema["properties"]["tool_calls"]["items"]["anyOf"]
        add_track = tool_schemas[2]
        synth = add_track["properties"]["arguments"]["properties"]["synth"]
        self.assertIn("voice", synth["required"])
        self.assertIn("tenor_sax", synth["properties"]["voice"]["enum"])
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
        raw = provider.generate("test prompt", {})
        self.assertEqual(parse_model_plan(raw).schema_version, "1.0")
        self.assertEqual(provider.audit_record(), envelope)
        repaired = provider.repair(
            prompt="test prompt",
            previous_response=raw,
            validation_error="velocity must be between 0.0 and 1.0; got 42.0",
            tool_manifest={},
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


class RuntimeTests(unittest.TestCase):
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

    def test_live_provider_repairs_invalid_plan_inside_one_run(self) -> None:
        invalid = _short_plan()
        invalid["tool_calls"][2]["arguments"]["velocity"] = 42
        valid = _short_plan()

        class RepairingProvider:
            provider_name = "test-live"
            model_name = "test-model"

            def __init__(self) -> None:
                self.repairs: list[dict] = []

            def generate(self, prompt, tool_manifest):
                del prompt, tool_manifest
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
            self.assertEqual(report["planning"], {"attempts": 2, "repaired": True})
            self.assertEqual(len(provider.repairs), 1)
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
