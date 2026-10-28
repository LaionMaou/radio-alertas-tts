"""CLI para probar y disparar una alerta puntual."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from .config import VALID_KINDS, VALID_TRANSPORTS, load_config, validate_config
from .errors import ConfigError
from .logging_setup import force_utf8_stdio, setup_logging
from .models import AlertRequest
from .service import AlertService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radio-alerts-tts",
        description="Genera una alerta TTS y la transmite por el WebDJ de AzuraCast.",
    )
    parser.add_argument("text", nargs="?", help="Texto japonés de la alerta")
    parser.add_argument("--text-file", type=Path, help="Leer el texto desde un archivo")
    parser.add_argument("--kind", choices=VALID_KINDS, default="other")
    parser.add_argument("--magnitude", type=float)
    parser.add_argument("--title", default="")
    parser.add_argument("--dedupe-key", default="")
    parser.add_argument("--dry-run", action="store_true", help="Generar audio sin transmitir")
    parser.add_argument("--transport", choices=VALID_TRANSPORTS)
    parser.add_argument("--no-discord", action="store_true")
    return parser


def _read_text(args: argparse.Namespace) -> str:
    sources = [bool(args.text), args.text_file is not None]
    if sum(sources) > 1:
        raise SystemExit("Usá el texto posicional o --text-file, no ambos")
    if args.text_file is not None:
        return args.text_file.read_text(encoding="utf-8")
    if args.text:
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Falta el texto de la alerta")


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = build_parser().parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        return 2

    overrides = {}
    if args.dry_run:
        overrides["dry_run"] = True
    if args.transport:
        overrides["transport"] = args.transport
    if args.no_discord:
        overrides["discord_webhook_url"] = ""

    if overrides:
        config = replace(config, **overrides)
        try:
            validate_config(config)
        except ConfigError as exc:
            print(f"Configuración inválida: {exc}", file=sys.stderr)
            return 2

    setup_logging(config.log_level, stream=sys.stderr)

    alert = AlertRequest(
        text=_read_text(args),
        kind=args.kind,
        magnitude=args.magnitude,
        title=args.title,
        dedupe_key=args.dedupe_key,
        source="cli",
    )

    try:
        result = AlertService(config).run_once(alert)
    except ValueError as exc:
        print(f"Alerta inválida: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.status in ("ok", "dry-run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
