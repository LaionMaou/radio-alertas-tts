"""CLI para disparar una alerta puntual o detener el bucle en curso."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, replace
from pathlib import Path

from .config import VALID_KINDS, VALID_TRANSPORTS, Config, load_config, validate_config
from .errors import ConfigError
from .logging_setup import force_utf8_stdio, setup_logging
from .models import VALID_TONES, AlertRequest
from .service import AlertService

STOP_COMMAND = "docker compose exec alerts-tts python -m radio_alerts_tts.cli stop"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radio-alerts-tts",
        description="Genera una alerta TTS y la transmite por el WebDJ de AzuraCast.",
    )
    sub = parser.add_subparsers(dest="command")

    speak = sub.add_parser("speak", help="Generar y transmitir una alerta")
    speak.add_argument("text", nargs="?", help="Texto japonés de la alerta")
    speak.add_argument("--text-file", type=Path, help="Leer el texto desde un archivo")
    speak.add_argument("--kind", choices=VALID_KINDS, default="other")
    speak.add_argument("--magnitude", type=float)
    speak.add_argument("--title", default="")
    speak.add_argument("--dedupe-key", default="")
    speak.add_argument("--tone", choices=sorted(VALID_TONES), default="soft")
    speak.add_argument("--loop", action="store_true", help="Repetir hasta detenerla")
    speak.add_argument("--preempt", action="store_true", help="Cortar lo que esté sonando")
    speak.add_argument("--dry-run", action="store_true", help="Generar audio sin transmitir")
    speak.add_argument("--transport", choices=VALID_TRANSPORTS)
    speak.add_argument("--no-discord", action="store_true")

    sub.add_parser("stop", help="Detener el bucle o la alerta en curso")
    return parser


def _normalize_argv(argv: list[str] | None) -> list[str]:
    items = list(sys.argv[1:] if argv is None else argv)
    if not items or items[0] in ("-h", "--help"):
        return items
    if items[0] not in ("speak", "stop"):
        items.insert(0, "speak")
    return items


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


def _stop(config: Config) -> int:
    """Habla con la API local: el token no viaja por el historial del shell."""
    if not config.api_token:
        print("API_TOKEN no configurado", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{config.api_port}/stop"
    request = urllib.request.Request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {config.api_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        print(f"La API respondió {exc.code}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"No se pudo contactar la API local: {exc}", file=sys.stderr)
        return 1

    print(body)
    return 0


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = build_parser().parse_args(_normalize_argv(argv))

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        return 2

    if args.command == "stop":
        return _stop(config)

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
        tone=args.tone,
        loop=args.loop,
        preempt=args.preempt,
        source="cli",
    )

    try:
        result = AlertService(config).run_once(alert)
    except ValueError as exc:
        print(f"Alerta inválida: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.status in ("ok", "dry-run", "stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
