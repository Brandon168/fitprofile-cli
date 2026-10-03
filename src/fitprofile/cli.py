"""Command line interface. Output is JSON on stdout; errors are JSON with exit code 1/2."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__, paths, report, store
from .client import ApiError, Client


class UsageError(Exception):
    pass


def load_env_file(path: Path) -> None:
    """Populate os.environ from KEY=value lines without shell expansion (real env wins)."""
    try:
        lines = path.read_text().splitlines()
        if path.stat().st_mode & 0o077:
            print(f"warning: {path} is readable by others; run: chmod 600 {path}", file=sys.stderr)
    except OSError:
        return
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if not sep or key.startswith("#"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def make_client(args: argparse.Namespace) -> Client:
    load_env_file(Path(args.env_file) if args.env_file else paths.default_env_file())
    email, password = os.environ.get("FITPROFILE_EMAIL"), os.environ.get("FITPROFILE_PASSWORD")
    if not email or not password:
        raise UsageError("Set FITPROFILE_EMAIL and FITPROFILE_PASSWORD (environment or "
                         f"{paths.default_env_file()}).")
    return Client(email, password, token_cache=paths.default_token_cache())


def emit(data: dict[str, Any]) -> int:
    print(json.dumps(data, indent=1, default=str))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    client = make_client(args)
    user = client.login(renew=args.renew_token)
    out = {"status": "auth_ok", "user_id": user.get("user_id")}
    if args.deep:  # a real measurement read: catches protocol/schema breakage before a sync does
        out.update(status="ok", **client.probe())
    return emit(out)


def cmd_sync(args: argparse.Namespace) -> int:
    path = Path(args.store)
    blob = store.sync(make_client(args), path, full=args.full)
    return emit({"status": "synced", "path": str(path), "records": blob["measurement_count"],
                 "resumed_profiles": blob["profiles_resumed"], "last_synced_at": blob["last_synced_at"]})


def cmd_export(args: argparse.Namespace) -> int:
    blob = store.sync(make_client(args), Path(args.store))
    out = paths.write_private(args.path, json.dumps(blob, indent=2, ensure_ascii=False))
    return emit({"status": "exported", "path": str(out), "profiles": len(blob["profiles"]),
                 "measurements": blob["measurement_count"]})


def cmd_profiles(args: argparse.Namespace) -> int:
    return emit({"profiles": make_client(args).profiles()})


def cmd_devices(args: argparse.Namespace) -> int:
    return emit({"devices": make_client(args).get("/device_binds/list_device_bind")})


def cmd_extras(args: argparse.Namespace) -> int:
    return emit({"extras": make_client(args).extras()})


def read_rows(args: argparse.Namespace) -> tuple[list[dict], dict[str, Any]]:
    """Records for the logged-in profile plus provenance: synced, store, or stale_store."""
    path, meta, blob = Path(args.store), {}, None
    if args.no_sync:
        blob = store.load_store(path)
        if blob is None:
            raise UsageError(f"No complete local store at {path}; run `fitprofile sync` first.")
        meta["origin"] = "store"
        uid = None
    else:
        client = make_client(args)
        uid = str(client.login()["user_id"])
        try:
            blob, meta["origin"] = store.sync(client, path), "synced"
        except ApiError as exc:
            blob = store.load_store(path)
            if blob is None:
                raise
            if blob.get("account_user_id") not in (None, uid):
                raise  # the store belongs to another account; never serve it as this one's
            meta.update(origin="stale_store", sync_note=f"sync failed: {exc}")
    meta.update(account_user_id=blob.get("account_user_id"), last_synced_at=blob.get("last_synced_at"),
                store_age_days=store.age_days(blob))
    if uid is None:  # offline: use the primary profile recorded in the store
        uid = next((str(p["user_id"]) for p in blob.get("profiles", []) if p.get("primary")), None)
    tz = report.zone(args.tz)
    records = [report.enrich(r, imperial=args.imperial, tz=tz) for r in store.rows(blob, uid)]
    return records, meta


def cmd_summary(args: argparse.Namespace) -> int:
    records, meta = read_rows(args)
    latest = records[-1] if records else {}
    return emit({"status": "ok", **meta, "records": len(records),
                 "summary": report.summarize(records, args.days, imperial=args.imperial,
                                             tz=report.zone(args.tz)),
                 "latest": {k: v for k, v in latest.items()
                            if k in report.HEADLINE or k in ("timestamp_local", "weight_lb")} or None})


def cmd_json(args: argparse.Namespace) -> int:
    records, meta = read_rows(args)
    return emit({**meta, "count": len(records), "measurements": records})


def cmd_csv(args: argparse.Namespace) -> int:
    records, meta = read_rows(args)
    out = paths.write_private(args.path, report.csv_text(records))
    return emit({"status": "csv_written", "path": str(out), "rows": len(records), **meta})


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fitprofile", description="Unofficial, read-only Fit Profile exporter.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--env-file", help="credentials file (default: ~/.config/fitprofile/credentials.env)")
    ap.add_argument("--store", default=str(paths.default_store()), help="local store path")
    ap.add_argument("--tz", default=os.environ.get("FITPROFILE_TZ"),
                    help="IANA zone for local timestamps (default: system zone)")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str, func, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.set_defaults(func=func)
        return p

    chk = add("check", cmd_check, "verify login")
    chk.add_argument("--renew-token", action="store_true")
    chk.add_argument("--deep", action="store_true", help="also read and validate one measurement page")
    add("sync", cmd_sync, "pull new records into the local store").add_argument(
        "--full", action="store_true", help="discard the cursor and re-pull everything")
    add("export", cmd_export, "sync, then write the full snapshot as JSON").add_argument("path")
    add("profiles", cmd_profiles, "list the account's profiles")
    add("extras", cmd_extras, "unassigned readings, girths and heart-rate records (live, not stored)")
    add("devices", cmd_devices, "list bound scales")
    for name, func, help in (("summary", cmd_summary, "recent weight and body-fat summary"),
                             ("json", cmd_json, "print measurements as JSON"),
                             ("csv", cmd_csv, "write measurements as CSV")):
        p = add(name, func, help)
        p.add_argument("--imperial", action="store_true", help="add pounds beside kilograms")
        p.add_argument("--no-sync", action="store_true", help="read the local store only; no network")
        if name == "summary":
            p.add_argument("--days", type=int, default=14)
        if name == "csv":
            p.add_argument("path")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}))
        return 2
    except (ApiError, OSError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
