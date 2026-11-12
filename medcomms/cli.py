"""The ``medcomms`` command.

medcomms dev                   everything on this computer, for development
medcomms front                 the front door (on the VM)
medcomms engine                a translation engine (on a GPU, e.g. Modal)
medcomms cloud start|stop|status|redeploy
                               manage engines on Modal from your laptop
medcomms users ...             user accounts
medcomms sessions ...          recorded sessions: list, export, delete
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .engine.config import DEVICES, DIRECTIONS, MODEL_SIZES

# Small, fast models for working on the page without waiting on downloads.
LIGHT_MODELS = {
    "asr": {"vi-en": "base", "en-vi": "base"},
    "mt": {
        "vi-en": "Helsinki-NLP/opus-mt-vi-en",
        "en-vi": "Helsinki-NLP/opus-mt-en-vi",
    },
}


def _model_override(value: str) -> tuple[str, str]:
    direction, sep, model = value.partition("=")
    if not sep or direction not in DIRECTIONS or not model:
        raise argparse.ArgumentTypeError(
            f"expected DIRECTION=MODEL with DIRECTION in {DIRECTIONS}, got {value!r}"
        )
    return direction, model


def _env(name: str, default=None):
    return os.environ.get(name) or default


def _add_model_options(p: argparse.ArgumentParser):
    p.add_argument(
        "--model-size",
        choices=MODEL_SIZES,
        default="small",
        help="size of the default models; 'large' needs a GPU (default: small)",
    )
    p.add_argument(
        "--asr",
        action="append",
        type=_model_override,
        default=[],
        metavar="DIRECTION=MODEL",
        help="speech recognition model for a direction, e.g. en-vi=large-v3",
    )
    p.add_argument(
        "--mt",
        action="append",
        type=_model_override,
        default=[],
        metavar="DIRECTION=MODEL",
        help="translation model for a direction, e.g. en-vi=Helsinki-NLP/opus-mt-en-vi",
    )


def _add_engine(commands):
    p = commands.add_parser(
        "engine",
        help="run a translation engine",
        description="Run the translation engine for one or both directions.",
    )
    p.add_argument("--host", default="0.0.0.0", help="(default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8100, help="(default: 8100)")
    p.add_argument("--device", choices=DEVICES, default="cpu", help="(default: cpu)")
    p.add_argument(
        "--direction",
        dest="directions",
        action="append",
        choices=DIRECTIONS,
        help="run only this direction (repeatable; default: both)",
    )
    _add_model_options(p)
    p.add_argument(
        "--silence-threshold",
        type=float,
        default=2.0,
        help="seconds of silence that end an utterance, min 1.5 (default: 2.0)",
    )
    p.add_argument(
        "--vad-aggressiveness",
        type=int,
        choices=range(10),
        default=8,
        metavar="0-9",
        help="higher = more confidence needed to count audio as speech (default: 8)",
    )
    p.add_argument(
        "--max-buffer-duration",
        type=int,
        choices=range(5, 31),
        default=15,
        metavar="5-30",
        help="longest sentence in seconds; longer speech is split (default: 15)",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="(default: INFO)",
    )
    p.set_defaults(handler=_run_engine)


def _add_front(commands):
    p = commands.add_parser(
        "front",
        help="run the front door (speaker page, accounts, recordings)",
        description=(
            "Serve the speaker page and relay sessions to the engines. "
            "Every option can also be set with the environment variable shown."
        ),
    )
    p.add_argument("--host", default=_env("MEDCOMMS_HOST", "127.0.0.1"),
                   help="[MEDCOMMS_HOST] (default: 127.0.0.1)")  # fmt: skip
    p.add_argument("--port", type=int, default=int(_env("PORT", 8000)),
                   help="[PORT] (default: 8000)")  # fmt: skip
    p.add_argument(
        "--engine",
        action="append",
        type=_model_override,
        default=[],
        metavar="DIRECTION=URL",
        help="WebSocket address of an engine, e.g. vi-en=ws://localhost:8100/ws/vi-en",
    )
    p.add_argument(
        "--modal-workspace",
        default=_env("MEDCOMMS_MODAL_WORKSPACE"),
        help="[MEDCOMMS_MODAL_WORKSPACE] use the engines deployed in this Modal "
        "workspace; needs MODAL_KEY and MODAL_SECRET (a proxy auth token)",
    )
    p.add_argument(
        "--signup-code",
        default=_env("MEDCOMMS_SIGNUP_CODE"),
        help="[MEDCOMMS_SIGNUP_CODE] lets new users create accounts (default: closed)",
    )
    _add_data_dir(p)
    p.add_argument(
        "--no-record",
        action="store_true",
        default=_env("MEDCOMMS_RECORD", "1") == "0",
        help="[MEDCOMMS_RECORD=0] don't record sessions",
    )
    p.add_argument(
        "--max-session-minutes",
        type=float,
        default=float(_env("MEDCOMMS_MAX_SESSION_MINUTES", 60)),
        help="[MEDCOMMS_MAX_SESSION_MINUTES] (default: 60)",
    )
    p.add_argument("--log-level", default=_env("MEDCOMMS_LOG_LEVEL", "INFO"),
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])  # fmt: skip
    p.set_defaults(handler=_run_front)


def _add_dev(commands):
    p = commands.add_parser(
        "dev",
        help="run everything on this computer",
        description=(
            "Run an engine and the front door together on this computer, "
            "with small models. Open http://localhost:8000."
        ),
    )
    p.add_argument(
        "--light",
        action="store_true",
        help="use tiny models: fast to download, poor translations (for page work)",
    )
    p.add_argument("--port", type=int, default=8000, help="(default: 8000)")
    p.add_argument(
        "--accounts",
        action="store_true",
        help="require sign-in, as on the VM (sign-up code: --signup-code)",
    )
    p.add_argument("--signup-code", default="dev", help="(default: dev)")
    _add_model_options(p)
    p.set_defaults(handler=_run_dev)


def _add_data_dir(p: argparse.ArgumentParser):
    p.add_argument(
        "--data-dir",
        default=_env("MEDCOMMS_DATA_DIR", "data"),
        help="[MEDCOMMS_DATA_DIR] accounts and recordings (default: data)",
    )


def _add_users(commands):
    p = commands.add_parser(
        "users",
        help="list, add, reset, disable or remove user accounts",
        description="User accounts. On the VM, run as the medcomms user.",
    )
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="every account, when created and last signed in")
    for action, text in [
        ("add", "create an account and print its password"),
        ("reset", "give an account a new password (signs it out everywhere)"),
        ("disable", "stop an account from signing in (keeps its recordings)"),
        ("enable", "let a disabled account sign in again"),
        ("remove", "delete an account (keeps its recordings)"),
    ]:
        sub.add_parser(action, help=text).add_argument("username")
    for parser in [p, *sub.choices.values()]:
        if parser is not p:
            _add_data_dir(parser)
    p.set_defaults(handler=_run_users)


def _add_sessions(commands):
    p = commands.add_parser(
        "sessions",
        help="list, export or delete recorded sessions",
        description="Recorded sessions. On the VM, run as the medcomms user.",
    )
    sub = p.add_subparsers(dest="action", required=True)

    def filters(q):
        q.add_argument("--user", help="only this user's sessions")
        q.add_argument("--since", metavar="YYYY-MM-DD", help="from this day")
        q.add_argument("--until", metavar="YYYY-MM-DD", help="up to this day")
        _add_data_dir(q)

    filters(sub.add_parser("list", help="sessions, newest first, with a summary"))
    export = sub.add_parser(
        "export",
        help="write sessions to a zip: their files plus sessions.csv and sentences.csv",
    )
    export.add_argument("output", help="the zip file to write, e.g. sessions.zip")
    filters(export)
    delete = sub.add_parser("delete", help="permanently delete sessions")
    delete.add_argument("session_ids", nargs="+", metavar="SESSION")
    delete.add_argument("--yes", action="store_true", help="don't ask to confirm")
    _add_data_dir(delete)
    p.set_defaults(handler=_run_sessions)


def _add_cloud(commands):
    p = commands.add_parser(
        "cloud",
        help="start, stop or check the engines on Modal",
        description="Manage translation engines on Modal. Needs `modal setup` once.",
    )
    sub = p.add_subparsers(dest="action", required=True)

    start = sub.add_parser(
        "start",
        help="turn a direction on, or change its models or the machine",
        description=(
            "Turn a direction on. All directions share one engine on one GPU; "
            "options you leave out keep their current value."
        ),
    )
    start.add_argument("direction", choices=DIRECTIONS)
    start.add_argument("--asr", default="", metavar="MODEL",
                       help="speech recognition model (default: by size)")  # fmt: skip
    start.add_argument("--mt", default="", metavar="MODEL",
                       help="translation model (default: VinAI Translate)")  # fmt: skip
    machine = start.add_argument_group("machine (shared by all directions)")
    machine.add_argument("--model-size", choices=MODEL_SIZES,
                         help="default models' size (initially: large)")  # fmt: skip
    machine.add_argument(
        "--gpu",
        help="GPU type, or several in order of preference (initially: L4,A10)",
    )
    machine.add_argument("--cpu", type=float, help="CPU cores (initially: 2)")
    machine.add_argument("--memory", type=int, metavar="GIB", dest="memory_gb",
                         help="memory in GiB (initially: 16)")  # fmt: skip
    machine.add_argument("--idle-minutes", type=float,
                         help="sleep after this long without a conversation "
                         "(initially: 5)")  # fmt: skip

    stop = sub.add_parser("stop", help="turn a direction off")
    stop.add_argument("direction", choices=(*DIRECTIONS, "all"))

    sub.add_parser("status", help="show which directions are on, and their models")
    sub.add_parser("redeploy", help="update the directions that are on to this code")
    p.set_defaults(handler=_run_cloud)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medcomms",
        description=(
            "MedComms (research prototype): "
            "Vietnamese ⇄ English medical speech translation."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"medcomms {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    _add_dev(commands)
    _add_front(commands)
    _add_engine(commands)
    _add_cloud(commands)
    _add_users(commands)
    _add_sessions(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args) or 0


# Handlers -------------------------------------------------------------------


def _run_engine(args) -> int:
    from .engine.config import Config
    from .engine.server import Engine
    from .log import setup_logging

    setup_logging(args.log_level)
    cfg = Config(
        device=args.device,
        model_size=args.model_size,
        directions=tuple(dict.fromkeys(args.directions or DIRECTIONS)),
        asr_models=dict(args.asr),
        mt_models=dict(args.mt),
        host=args.host,
        port=args.port,
        silence_threshold=args.silence_threshold,
        vad_aggressiveness=args.vad_aggressiveness,
        max_buffer_duration=args.max_buffer_duration,
        log_level=args.log_level,
    )
    return Engine(cfg).run()


def _front_config(args):
    from .cloud import engine_url
    from .front.app import FrontConfig

    engines, headers = {}, {}
    if args.modal_workspace:
        engines = {d: engine_url(args.modal_workspace, d) for d in DIRECTIONS}
        key, secret = os.environ.get("MODAL_KEY"), os.environ.get("MODAL_SECRET")
        if not (key and secret):
            raise SystemExit("MODAL_KEY and MODAL_SECRET must be set to reach Modal")
        headers = {"Modal-Key": key, "Modal-Secret": secret}
    engines.update(dict(args.engine))
    return FrontConfig(
        engines=engines,
        engine_headers=headers,
        data_dir=Path(args.data_dir),
        signup_code=args.signup_code or None,
        record=not args.no_record,
        host=args.host,
        port=args.port,
        max_session_minutes=args.max_session_minutes,
    )


def _run_front(args) -> int:
    from .front.app import run
    from .log import setup_logging

    setup_logging(args.log_level)
    run(_front_config(args))
    return 0


def _run_dev(args) -> int:
    from .front.app import FrontConfig, run
    from .log import setup_logging

    setup_logging("INFO")
    asr = {**(LIGHT_MODELS["asr"] if args.light else {}), **dict(args.asr)}
    mt = {**(LIGHT_MODELS["mt"] if args.light else {}), **dict(args.mt)}
    engine_port = args.port + 100
    cmd = [sys.executable, "-m", "medcomms", "engine", "--host", "127.0.0.1"]
    cmd += ["--port", str(engine_port), "--model-size", args.model_size]
    for d, m in asr.items():
        cmd += ["--asr", f"{d}={m}"]
    for d, m in mt.items():
        cmd += ["--mt", f"{d}={m}"]

    engine = subprocess.Popen(cmd)
    try:
        run(
            FrontConfig(
                engines={d: f"ws://127.0.0.1:{engine_port}/ws/{d}" for d in DIRECTIONS},
                data_dir=Path("data"),
                accounts=args.accounts,
                signup_code=args.signup_code if args.accounts else None,
                host="127.0.0.1",
                port=args.port,
            )
        )
    finally:
        engine.terminate()
        engine.wait(timeout=30)
    return 0


def _run_cloud(args) -> int:
    from .cloud import control

    if args.action == "start":
        control.start(
            args.direction,
            asr=args.asr,
            mt=args.mt,
            model_size=args.model_size,
            gpu=args.gpu,
            cpu=args.cpu,
            memory_gb=args.memory_gb,
            idle_minutes=args.idle_minutes,
        )
    elif args.action == "stop":
        control.stop(args.direction)
    elif args.action == "status":
        control.status()
    else:
        control.redeploy()
    return 0


def _run_users(args) -> int:
    from .front.manage import users

    return users(args)


def _run_sessions(args) -> int:
    from .front.manage import sessions

    return sessions(args)
