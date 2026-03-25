from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.main import app
from backend.app.core.settings import get_settings


def write_openapi(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export the backend OpenAPI schema")
    parser.add_argument(
        "--output",
        type=Path,
        default=get_settings().openapi_output_path,
        help="Destination path for openapi.json",
    )
    parser.add_argument(
        "--frontend-output",
        type=Path,
        default=Path("frontend/src/types/api-schema.json"),
        help="Optional frontend schema output path",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    write_openapi(args.output)
    write_openapi(args.frontend_output)
    print(f"OpenAPI written to {args.output}")
    print(f"OpenAPI mirrored to {args.frontend_output}")


if __name__ == "__main__":
    main()
