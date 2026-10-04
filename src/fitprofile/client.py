"""Client for the Fit Profile cloud API (unofficial; see docs/protocol.md)."""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .paths import write_private
from .report import sort_key

HOST = "https://fit-profile.qnclouds.com"
API_BASE = f"{HOST}/api/v4"
# Some services live under their own prefix on the same host, relative to HOST not API_BASE.
HOST_PREFIXES = ("/measurement/api/v4/",)
APP_ID = "fit_profile"
USER_AGENT = f"fitprofile-cli/{__version__} (unofficial)"
SUCCESS = {"200"}
# The service has destructive operations that are plain GETs (for example deleting a health
# report), so a method restriction is not enough. Only these reads can be requested.
ALLOWED_READS = frozenset({
    "/users/get_primary_user", "/sub_users/list_sub_user", "/measurements/list_measurement",
    "/device_binds/list_device_bind", "/goals/list_goal", "/user_settings/show_common_setting",
    "/unknown_datas/list_unknown_data", "/girths/list_girth", "/girths/list_custom_girth",
    "/heart_rate_records/list_heart_rate_record", "/bodyfat_calculations/list_bodyfat_calculation",
    "/device_users/list_device_user", "/scale_users/list_scale_user", "/weight_predicts/list_weight_predict",
    "/health_reports/list_health_report", "/health_reports/show_health_report",
    "/measurement/api/v4/measurement_weeklies/list_measurement_weekly",
    "/measurement/api/v4/measurement_weeklies/show_measurement_weekly",
    "/measurement/api/v4/ai_weight_goal_reports/show_report",
    "/measurement/api/v4/check_data/check_weekly_report_exists",
})
# State-changing calls. These are plain GETs on the service, so they get their own allowlist and
# can only be reached through the explicit methods below. UNTESTED against the live service.
ALLOWED_WRITES = frozenset({"/health_reports/delete_health_report"})
# Cursor-paged secondary datasets: path -> (record key, deleted-ids key, id-cursor parameter)
PAGED = {
    "girths": ("/girths/list_girth", "girth_ary", "deleted_girth_ids", "last_girth_id"),
    "heart_rates": ("/heart_rate_records/list_heart_rate_record", "heart_rate_records",
                    "deleted_heart_rate_record_ids", "last_heart_rate_record_id"),
    "bodyfat_calculations": ("/bodyfat_calculations/list_bodyfat_calculation", "bodyfat_calculations",
                             "deleted_bodyfat_calculation_ids", "last_bodyfat_calculation_id"),
}
# Public RSA key the service uses to receive passwords at login. It is a public key
# (not a secret) and is the same one other community clients for this service use.
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC+25I2upukpfQ7rIaaTZtVE744
u2zV+HaagrUhDOTq8fMVf9yFQvEZh2/HKxFudUxP0dXUa8F6X4XmWumHdQnum3zm
Jr04fz2b2WCcN0ta/rbF2nYAnMVAk2OJVZAMudOiMWhcxV1nNJiKgTNNr13de0EQ
IiOL2CUBzu+HmIfUbQIDAQAB
-----END PUBLIC KEY-----"""

Transport = Callable[[urllib.request.Request], "tuple[int, bytes, str]"]


class ApiError(RuntimeError):
    pass


class TokenExpired(ApiError):
    pass


def urlopen_transport(req: urllib.request.Request) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), r.headers.get("Content-Encoding", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Encoding", "")


def encrypt_password(password: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)
    return base64.b64encode(key.encrypt(password.encode(), padding.PKCS1v15())).decode()


class Client:
    def __init__(self, email: str, password: str, *, token_cache: Path | None = None,
                 locale: str = "en", transport: Transport = urlopen_transport,
                 sleep: Callable[[float], None] = time.sleep):
        self.email, self.password, self.token_cache = email, password, token_cache
        self.locale, self.transport, self.sleep = locale, transport, sleep
        self.token: str | None = None
        self.user: dict[str, Any] = {}

    # -- transport ---------------------------------------------------------
    def _call(self, path: str, *, body: dict | None = None, **params: str) -> Any:
        query = {"app_id": APP_ID, "locale": self.locale, **params}
        headers = {"Accept-Encoding": "gzip", "User-Agent": USER_AGENT,
                   "Authorization": f"Bearer {self.token}" if self.token else "Bearer"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json;charset=UTF-8"
            data = json.dumps(body).encode()
        base = HOST if path.startswith(HOST_PREFIXES) else API_BASE
        req = urllib.request.Request(f"{base}{path}?{urllib.parse.urlencode(query)}", data=data,
                                     headers=headers, method="POST" if body is not None else "GET")
        for attempt in range(3):
            try:
                status, raw, encoding = self.transport(req)
            except OSError as exc:
                status, raw, encoding, failure = 0, b"", "", type(exc).__name__
            else:
                failure = f"HTTP {status}"
            if status and status < 500:
                break
            if attempt < 2:
                self.sleep(2 ** attempt)
        else:
            raise ApiError(f"{failure} on {path} after 3 attempts")
        if status >= 400:
            raise ApiError(f"HTTP {status} on {path}")
        try:
            result = json.loads(gzip.decompress(raw) if encoding.lower() == "gzip" else raw)
            code = str(result.get("code"))
        except (ValueError, AttributeError) as exc:
            # Never echo raw bodies: they can contain tokens or personal data.
            raise ApiError(f"Invalid JSON response on {path}") from exc
        if code == "403":
            # The app's own text for 403 is a JWT failure: expired, revoked, or a skewed clock.
            raise TokenExpired("Authentication failed (403): token expired or revoked, or system clock is wrong")
        if code not in SUCCESS:
            raise ApiError(f"{path} failed: code={code} msg={result.get('msg', '')}")
        if isinstance(result.get("data"), str):
            raise ApiError(f"{path} returned a string payload (possibly encrypted, or the schema changed)")
        return result.get("data")

    # -- auth --------------------------------------------------------------
    def login(self, *, renew: bool = False) -> dict[str, Any]:
        """Log in, reusing the cached bearer token (lifetime set by the server's `remaining_time`) while it is valid."""
        if not renew and self.token_cache:
            try:
                blob = json.loads(self.token_cache.read_text())
                if blob["email"] == self.email and time.time() < blob["expires_at"] - 86400:
                    self.token, self.user = blob["token"], blob["user_info"]
                    return self.user
            except (OSError, ValueError, KeyError, TypeError):
                pass
        self.token = None
        data = self._call("/users/sign_in", body={"email": self.email,
                                                  "password": encrypt_password(self.password)}) or {}
        info = data.get("token_info") or {}
        user = data.get("user_info") or {}
        if not info.get("token") or not user.get("user_id"):
            raise ApiError("Login response lacks a token or user ID")
        self.token, self.user = info["token"], user
        if self.token_cache:
            try:
                write_private(self.token_cache, json.dumps({
                    "email": self.email, "token": self.token, "user_info": user,
                    "expires_at": time.time() + float(info.get("remaining_time") or 0)}))
            except OSError:
                pass  # An unwritable cache must not break a read.
        return user

    def get(self, path: str, **params: str) -> Any:
        if path not in ALLOWED_READS:
            raise ApiError(f"{path} is not an allowed read endpoint")
        return self._authed(path, **params)

    def _authed(self, path: str, **params: str) -> Any:
        if not self.token:
            self.login()
        try:
            return self._call(path, **params)
        except TokenExpired:
            self.login(renew=True)
            return self._call(path, **params)

    # -- data --------------------------------------------------------------
    def profiles(self) -> list[dict]:
        subs = self.get("/sub_users/list_sub_user")
        subs = subs.get("sub_users") if isinstance(subs, dict) else subs
        if not isinstance(subs, list):
            raise ApiError("Unexpected sub-user list schema")
        # The login payload can be months old (cached token); refresh the primary profile.
        fresh = self.get("/users/get_primary_user")
        fresh = fresh.get("user_info") if isinstance(fresh, dict) else None
        primary = {**self.user, **(fresh if isinstance(fresh, dict) else {})}
        found = {str(self.user["user_id"]): {**primary, "primary": True}}
        for sub in subs:
            found.setdefault(str(sub["user_id"]), {**sub, "primary": False})
        return list(found.values())

    def history(self, user_id: str, last_updated_at: str = "0", last_measurement_id: str = "0") -> dict:
        """Walk the cursor chain to the end; any inconsistency raises instead of returning a partial history."""
        rows: dict[str, dict] = {}
        deleted: set[str] = set()
        seen: set[tuple] = set()
        cursor = (str(last_updated_at), str(last_measurement_id))
        for page in range(1, 10001):
            if cursor in seen:
                raise ApiError("Measurement pagination stalled; refusing a partial export")
            seen.add(cursor)
            data = self.get("/measurements/list_measurement", user_id=user_id,
                            last_updated_at=cursor[0], last_measurement_id=cursor[1])
            if not isinstance(data, dict) or not isinstance(data.get("measurements"), list):
                raise ApiError("Unexpected measurement page schema")
            check_page(data)
            rows.update({str(r["measurement_id"]): r for r in data["measurements"]})
            deleted.update(str(x) for x in data.get("delete_measurement_ids") or [])
            cursor = (str(data["last_updated_at"]), str(data["last_measurement_id"]))
            flag = str(data.get("finish_flag"))
            if flag == "1":
                return _history(rows.values(), deleted, cursor, pages=page)
            if flag != "0":
                raise ApiError("Unknown finish_flag; refusing a partial export")
        raise ApiError("Measurement page limit reached; refusing a partial export")

    def delete_health_report(self, user_id: str, record_date: str) -> Any:
        """Delete one monthly health report. UNTESTED live: parameters are inferred from the read endpoints."""
        path = "/health_reports/delete_health_report"
        assert path in ALLOWED_WRITES
        return self._authed(path, user_id=user_id, record_date=record_date)

    def paged(self, name: str, user_id: str) -> dict:
        """Walk one secondary cursor-paged dataset (girths, heart_rates) to its end; never returns a partial list."""
        path, rec_key, del_key, id_param = PAGED[name]
        rows: list[Any] = []
        deleted: set[str] = set()
        cursor = ("0", "0")
        seen: set[tuple] = set()
        for _ in range(10001):
            if cursor in seen:
                raise ApiError(f"{name} pagination stalled; refusing a partial export")
            seen.add(cursor)
            data = self.get(path, user_id=user_id, last_updated_at=cursor[0], **{id_param: cursor[1]})
            if not isinstance(data, dict) or not isinstance(data.get(rec_key), list):
                raise ApiError(f"Unexpected {name} page schema")
            rows.extend(data[rec_key])
            deleted.update(str(x) for x in data.get(del_key) or [])
            cursor = (str(data["last_updated_at"]), str(data[id_param]))
            flag = str(data.get("finish_flag"))
            if flag == "1":
                return {"records": rows, "count": len(rows), "deleted_ids": sorted(deleted)}
            if flag != "0":
                raise ApiError(f"Unknown finish_flag in {name}; refusing a partial export")
        raise ApiError(f"{name} page limit reached; refusing a partial export")

    def extras(self, zone: str = "UTC") -> dict:
        """Optional read-only datasets outside the weight history (unassigned readings, girths, heart rates,
        body-fat calculations, weight prediction, and the user slots on each bound scale)."""
        profiles = self.profiles()
        binds = self.get("/device_binds/list_device_bind")
        binds = binds.get("device_binds") if isinstance(binds, dict) else None
        out: dict[str, Any] = {"unassigned": self.get("/unknown_datas/list_unknown_data"), "profiles": {},
                               "devices": {}}
        for b in binds or []:
            mac = b.get("mac")
            if mac:
                out["devices"][mac] = {"device_users": self.get("/device_users/list_device_user", mac=mac),
                                       "scale_users": self.get("/scale_users/list_scale_user", mac=mac)}
        for p in profiles:
            uid = str(p["user_id"])
            out["profiles"][uid] = {
                "girths": self.paged("girths", uid),
                "custom_girths": self.get("/girths/list_custom_girth", user_id=uid),
                "heart_rates": self.paged("heart_rates", uid),
                "bodyfat_calculations": self.paged("bodyfat_calculations", uid),
                "weight_predict": self.get("/weight_predicts/list_weight_predict", user_id=uid, zone=zone)}
        return out

    def reports(self, user_id: str, *, today: dt.date | None = None) -> dict:
        """Existing monthly and weekly report indexes for one profile (reads only; nothing is generated)."""
        day = (today or dt.date.today()).replace(day=1).isoformat()
        # Observed: the monthly index ignores record_date and lists the latest month, so one call suffices.
        monthly = self.get("/health_reports/list_health_report", user_id=user_id, record_date=day)
        weekly = self.get("/measurement/api/v4/measurement_weeklies/list_measurement_weekly",
                          user_id=user_id, limit="50", page="1")
        return {"monthly": monthly, "weekly": weekly}

    def probe(self) -> dict:
        """Read-only health check: login, profiles, and one real measurement page validated for shape."""
        profiles = self.profiles()
        page = self.get("/measurements/list_measurement", user_id=str(self.user["user_id"]),
                        last_updated_at="0", last_measurement_id="0")
        check_page(page)
        return {"profiles": len(profiles), "first_page_records": len(page["measurements"]),
                "finish_flag": page.get("finish_flag")}

    def snapshot(self, prior: dict | None = None) -> dict:
        """Profiles, full histories, devices, goals and settings. Resumes complete histories from `prior`."""
        profiles = self.profiles()
        account = str(self.user["user_id"])
        if prior and prior.get("account_user_id") not in (None, account):
            prior = None  # cursors belong to one account; never resume another's
        histories, resumed = {}, 0
        for p in profiles:
            uid = str(p["user_id"])
            old = ((prior or {}).get("histories") or {}).get(uid)
            if old and old.get("complete"):
                fresh = self.history(uid, old["last_updated_at"], old["last_measurement_id"])
                histories[uid] = _merge(old, fresh)
                resumed += 1
            else:
                histories[uid] = self.history(uid)
        for uid, old in ((prior or {}).get("histories") or {}).items():
            if uid not in histories and old.get("complete"):
                histories[uid] = old  # profile no longer listed: keep what we have
        return {"schema_version": 1, "complete": True, "app_id": APP_ID, "account_user_id": account,
                "profiles_resumed": resumed, "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "profiles": profiles, "histories": histories,
                "measurement_count": sum(h["count"] for h in histories.values()),
                "devices": self.get("/device_binds/list_device_bind"),
                "goals": {str(p["user_id"]): self.get("/goals/list_goal", user_id=str(p["user_id"]))
                          for p in profiles},
                "settings": self.get("/user_settings/show_common_setting")}


REQUIRED_PAGE_KEYS = ("last_updated_at", "last_measurement_id", "finish_flag")
REQUIRED_RECORD_KEYS = ("measurement_id", "time_stamp", "weight")


def check_page(data: Any) -> None:
    """Raise ApiError, not KeyError, when a measurement page or record has an unexpected shape."""
    if not isinstance(data, dict) or not isinstance(data.get("measurements"), list):
        raise ApiError("Unexpected measurement page schema")
    missing = [k for k in REQUIRED_PAGE_KEYS if k not in data]
    if missing:
        raise ApiError(f"Measurement page lacks {', '.join(missing)}; schema changed")
    for r in data["measurements"]:
        if not isinstance(r, dict) or any(k not in r for k in REQUIRED_RECORD_KEYS):
            raise ApiError("Measurement record lacks measurement_id/time_stamp/weight; schema changed")


def _history(rows, deleted: set[str], cursor: tuple, *, pages: int | None = None) -> dict:
    kept = sorted((r for r in rows if str(r["measurement_id"]) not in deleted), key=sort_key)
    out = {"measurements": kept, "count": len(kept), "complete": True,
           "last_updated_at": cursor[0], "last_measurement_id": cursor[1],
           "delete_measurement_ids": sorted(deleted)}
    if pages:
        out["pages"] = pages
    return out


def _merge(old: dict, new: dict) -> dict:
    """Union a resumed page set into a stored history (a resume re-sends its boundary record)."""
    rows = {str(r["measurement_id"]): r for r in old.get("measurements", [])}
    rows.update({str(r["measurement_id"]): r for r in new.get("measurements", [])})
    gone = {str(x) for x in old.get("delete_measurement_ids", [])} | \
           {str(x) for x in new.get("delete_measurement_ids", [])}
    return _history(rows.values(), gone, (new["last_updated_at"], new["last_measurement_id"]))
