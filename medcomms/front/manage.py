"""``medcomms users …`` and ``medcomms sessions …``: looking after accounts and
recordings from the command line (on the VM, or on a downloaded copy)."""

import sys
from datetime import datetime
from pathlib import Path

from .sessions import SessionStore, export, summary
from .users import AccountError, UserStore, generate_password


def _table(rows: list[list[str]], header: list[str]):
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        print("  ".join(str(cell).ljust(w) for cell, w in zip(row, widths)).rstrip())


def _when(iso: str | None) -> str:
    if not iso:
        return "-"
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


# Users ----------------------------------------------------------------------


def users(args) -> int:
    store = UserStore(Path(args.data_dir))
    action = args.action

    if action == "list":
        accounts = store.all()
        if not accounts:
            print("No accounts yet.")
            return 0
        rows = [
            [
                name,
                _when(u.get("created")),
                _when(u.get("last_seen")),
                "disabled" if u.get("disabled") else "active",
            ]
            for name, u in sorted(accounts.items())
        ]
        _table(rows, ["username", "created", "last signed in", "status"])
        return 0

    name = args.username.strip().lower()
    if action == "add":
        password = generate_password()
        try:
            store.create(name, password)
        except AccountError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Created {name}. Password: {password}")
        return 0

    if store.get(name) is None:
        print(f"No account called {name}.", file=sys.stderr)
        return 1
    if action == "reset":
        password = generate_password()
        store.set_password(name, password)
        print(f"New password for {name}: {password}  (they are signed out everywhere)")
    elif action == "disable":
        store.update(name, disabled=True)
        print(f"{name} can no longer sign in. Their recordings are kept.")
    elif action == "enable":
        store.update(name, disabled=False)
        print(f"{name} can sign in again.")
    elif action == "remove":
        store.remove(name)
        print(f"Removed {name}. Their recordings are kept (tagged with the username).")
    return 0


# Sessions -------------------------------------------------------------------


def _selected(store: SessionStore, args) -> list[dict]:
    return store.sessions(user=args.user, since=args.since, until=args.until)


def sessions(args) -> int:
    store = SessionStore(Path(args.data_dir))

    if args.action == "list":
        found = _selected(store, args)
        if not found:
            print("No sessions.")
            return 0
        rows = []
        for s in found:
            row = summary(s)
            minutes = f"{row['duration_s'] / 60:.1f}" if row["duration_s"] else "-"
            rows.append(
                [
                    row["session"],
                    _when(row["started"]),
                    row["user"] or "-",
                    row["direction"],
                    minutes,
                    row["sentences"],
                    row["avg_translation_rating"] or "-",
                    row["corrections"],
                    f"{row['asr']} | {row['mt']}",
                ]
            )
        _table(
            rows,
            ["session", "started", "user", "direction", "min", "sentences",
             "rating", "corrections", "models (ASR | MT)"],
        )  # fmt: skip
        print(f"\n{len(found)} session(s)")
        return 0

    if args.action == "export":
        found = _selected(store, args)
        if not found:
            print("No sessions match.", file=sys.stderr)
            return 1
        path = export(found, Path(args.output))
        print(f"Wrote {len(found)} session(s) to {path}")
        return 0

    # delete
    targets = []
    for session_id in args.session_ids:
        if store.find(session_id) is None:
            print(f"No session {session_id}.", file=sys.stderr)
            return 1
        targets.append(session_id)
    if not args.yes:
        answer = input(
            f"Permanently delete {len(targets)} session(s) "
            "(audio, transcripts, feedback)? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return 1
    for session_id in targets:
        store.delete(session_id)
    print(f"Deleted {len(targets)} session(s).")
    return 0
