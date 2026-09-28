from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuo.cases import ArtifactStore, CaseValidationError, ResearchCase
from continuo.cli import main
from continuo.domain import DomainValidationError
from continuo.midi import write_midi
from continuo.openai_provider import OpenAIResponsesProvider, music_plan_schema
from continuo.planning import RecordedProvider, parse_model_plan
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
        raw = (ROOT / "eval" / "cases" / "cafe_jazz_60s.model.json").read_text(
            encoding="utf-8"
        )
        plan = parse_model_plan(raw)
        project = MusicToolRuntime().apply_plan(plan)
        project.validate(forbid_vocals=True)
        self.assertEqual(project.duration_seconds, 60)
        self.assertGreaterEqual(len(project.tracks), 4)
        self.assertGreater(sum(len(track.events) for track in project.tracks), 500)

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

    def test_openai_schema_is_strict_at_the_root(self) -> None:
        schema = music_plan_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        add_track = schema["properties"]["tool_calls"]["items"]["anyOf"][2]
        synth = add_track["properties"]["arguments"]["properties"]["synth"]
        self.assertIn("voice", synth["required"])

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
            artifacts = Path(directory) / "artifacts"
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "generate",
                        "--case",
                        str(ROOT / "eval" / "cases" / "cafe_jazz_60s.case.json"),
                        "--artifacts-root",
                        str(artifacts),
                        "--provider",
                        "recorded",
                    ]
                )
            self.assertEqual(exit_code, 0)
            result = json.loads(output.getvalue())
            run_path = Path(result["artifact_dir"])
            self.assertEqual(run_path.parent.parent, artifacts / "cafe_jazz_60s")
            self.assertEqual(result["run"]["case_id"], "cafe_jazz_60s")
            self.assertTrue((run_path / "audio.wav").exists())


if __name__ == "__main__":
    unittest.main()
