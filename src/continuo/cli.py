from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .composition import (
    DEFAULT_MODEL,
    OpenAIResponsesProvider,
    RecordedProvider,
    load_env_file,
)
from .rendering import ReferenceWavRenderer
from .rendering.soundfont.renderer import FluidSynthRenderer
from .rendering.supercollider import SuperColliderNrtRenderer
from .workflow import AgentRuntime, ArtifactStore, ResearchCase, RunPolicy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="continuo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="Generate a music project")
    input_group = generate.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--case", type=Path)
    input_group.add_argument("--prompt")
    generate.add_argument("--provider", choices=("recorded", "openai"), default="recorded")
    generate.add_argument("--recorded-response", type=Path)
    generate.add_argument("--env-file", type=Path, default=Path(".env"))
    generate.add_argument("--model")
    generate.add_argument(
        "--soundfont-mapping-model",
        help="model used to map score tracks to the active SoundFont",
    )
    generate.add_argument("--output-dir", type=Path)
    generate.add_argument("--artifacts-root", type=Path, default=Path("artifacts"))
    generate.add_argument("--expected-duration", type=float)
    generate.add_argument("--forbid-vocals", action="store_true", default=None)
    generate.add_argument(
        "--backend",
        choices=("python", "supercollider", "soundfont"),
        default="soundfont",
        help="rendering backend (default: soundfont; others are development tools)",
    )
    generate.add_argument("--fluidsynth-executable", type=Path)
    generate.add_argument("--soundfont", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "generate":
        run_id = None
        case_id = None
        case = None
        recorded_response = args.recorded_response
        if args.case is not None:
            if args.output_dir is not None:
                raise SystemExit("--output-dir cannot be used with --case")
            if args.expected_duration is not None or args.forbid_vocals is not None:
                raise SystemExit(
                    "duration and vocal policy belong in the case file when --case is used"
                )
            if args.recorded_response is not None:
                raise SystemExit("--recorded-response belongs in the case file")
            case = ResearchCase.load(args.case)
            prompt = case.prompt
            output_dir = None
            recorded_response = case.recorded_response_path()
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
        else:
            if args.output_dir is None:
                raise SystemExit("--output-dir is required when --prompt is used")
            prompt = args.prompt
            output_dir = args.output_dir
            policy = RunPolicy(
                expected_duration_seconds=args.expected_duration,
                forbid_vocals=bool(args.forbid_vocals),
            )
        if args.provider == "openai":
            load_env_file(args.env_file)
            provider = OpenAIResponsesProvider(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                model=(
                    args.model
                    or (case.model if case is not None else None)
                    or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
                ),
                soundfont_mapping_model=(
                    args.soundfont_mapping_model
                    or os.environ.get("OPENAI_SOUNDFONT_MAPPING_MODEL")
                ),
            )
        else:
            if recorded_response is None:
                raise SystemExit(
                    "the recorded provider requires recorded_response in the case file "
                    "or --recorded-response in prompt mode"
                )
            provider = RecordedProvider(recorded_response)
        if args.backend == "supercollider":
            renderer = SuperColliderNrtRenderer()
        elif args.backend == "soundfont":
            renderer = FluidSynthRenderer(
                executable=args.fluidsynth_executable,
                soundfont=args.soundfont,
            )
        else:
            renderer = ReferenceWavRenderer()
        if case is not None:
            workspace = ArtifactStore(args.artifacts_root).create_run(case)
            output_dir = workspace.path
            run_id = workspace.run_id
            case_id = workspace.case_id
        assert output_dir is not None
        report = AgentRuntime(renderer=renderer).run(
            prompt=prompt,
            provider=provider,
            output_dir=output_dir,
            policy=policy,
            run_id=run_id,
            case_id=case_id,
        )
        result = {"artifact_dir": str(output_dir.resolve()), **report}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
