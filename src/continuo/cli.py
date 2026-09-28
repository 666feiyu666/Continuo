from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .openai_provider import DEFAULT_MODEL, OpenAIResponsesProvider, load_env_file
from .planning import RecordedProvider
from .rendering import ReferenceWavRenderer
from .runtime import AgentRuntime, RunPolicy
from .supercollider import SuperColliderNrtRenderer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="continuo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="Generate a music project")
    generate.add_argument("--prompt", required=True)
    generate.add_argument("--provider", choices=("recorded", "openai"), default="recorded")
    generate.add_argument("--recorded-response", type=Path)
    generate.add_argument("--env-file", type=Path, default=Path(".env"))
    generate.add_argument("--model")
    generate.add_argument("--output-dir", required=True, type=Path)
    generate.add_argument("--expected-duration", type=float)
    generate.add_argument("--forbid-vocals", action="store_true")
    generate.add_argument(
        "--backend",
        choices=("python", "supercollider"),
        default="python",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "generate":
        if args.provider == "openai":
            load_env_file(args.env_file)
            provider = OpenAIResponsesProvider(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                model=args.model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
            )
        else:
            if args.recorded_response is None:
                raise SystemExit("--recorded-response is required for the recorded provider")
            provider = RecordedProvider(args.recorded_response)
        renderer = (
            SuperColliderNrtRenderer()
            if args.backend == "supercollider"
            else ReferenceWavRenderer()
        )
        report = AgentRuntime(renderer=renderer).run(
            prompt=args.prompt,
            provider=provider,
            output_dir=args.output_dir,
            policy=RunPolicy(
                expected_duration_seconds=args.expected_duration,
                forbid_vocals=args.forbid_vocals,
            ),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
