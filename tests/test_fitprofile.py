"""Offline tests. Every record, credential and token here is synthetic."""
import contextlib
import datetime as dt
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from fitprofile import cli, client as fp, report, store

EMAIL, PASSWORD = "user@example.invalid", "p@ss$word"


def ok(data):
    return {"code": 200, "msg": "ok", "data": data}


def row(mid, ts, weight=70.0, **extra):
    return {"measurement_id": str(mid), "time_stamp": ts, "weight": weight, **extra}


def page(rows, *, finish, lu="1", lm="1", deleted=()):
    return ok({"measurements": rows, "delete_measurement_ids": list(deleted),
               "last_updated_at": lu, "last_measurement_id": lm, "finish_flag": finish})


class FakeTransport:
    """Serves queued JSON responses per path and records every request."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests = []

    def __call__(self, req):
        path = urllib.parse.urlsplit(req.full_url).path.removeprefix("/api/v4")
        self.requests.append(req)
        item = self.routes[path].pop(0) if len(self.routes[path]) > 1 else self.routes[path][0]
        if isinstance(item, Exception):
            raise item
        status, body = item if isinstance(item, tuple) else (200, item)
        return status, json.dumps(body).encode(), ""

    def query(self, i=-1):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.requests[i].full_url).query))

    def measurement_queries(self):
        return [self.query(i) for i, r in enumerate(self.requests) if "list_measurement" in r.full_url]


LOGIN = ok({"token_info": {"token": "tok-1", "remaining_time": 86400 * 180},
            "user_info": {"user_id": "7", "nickname": "tester"}})
BASE_ROUTES = {
    "/users/sign_in": [LOGIN],
    "/sub_users/list_sub_user": [ok({"sub_users": []})],
    "/device_binds/list_device_bind": [ok({"device_binds": []})],
    "/goals/list_goal": [ok({"goals": []})],
    "/user_settings/show_common_setting": [ok({})],
}


def make(routes=None, cache=None, **kw):
    t = FakeTransport({**BASE_ROUTES, **(routes or {})})
    return fp.Client(EMAIL, PASSWORD, token_cache=cache, transport=t, sleep=lambda s: None, **kw), t


class RequestShape(unittest.TestCase):
    def test_password_is_rsa_encrypted_and_body_is_plain_json(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
        c, t = make()
        with mock.patch.object(fp, "PUBLIC_KEY_PEM", pem):
            c.login()
        body = json.loads(t.requests[0].data)
        self.assertEqual(set(body), {"email", "password"})
        self.assertNotIn(PASSWORD, json.dumps(body))
        import base64
        plain = private.decrypt(base64.b64decode(body["password"]), padding.PKCS1v15())
        self.assertEqual(plain.decode(), PASSWORD)

    def test_minimal_query_and_honest_user_agent(self):
        c, t = make()
        c.login()
        self.assertEqual(t.query(0), {"app_id": "fit_profile", "locale": "en"})
        self.assertTrue(t.requests[0].get_header("User-agent").startswith("fitprofile-cli/"))
        self.assertNotIn("okhttp", t.requests[0].get_header("User-agent"))

    def test_encrypted_payload_is_rejected_clearly(self):
        c, _ = make({"/users/sign_in": [ok("c29tZS1jaXBoZXJ0ZXh0")]})
        with self.assertRaisesRegex(fp.ApiError, "protocol has changed"):
            c.login()

    def test_application_error_is_not_success(self):
        c, _ = make({"/users/sign_in": [{"code": 20101, "msg": "nope", "data": {}}]})
        with self.assertRaisesRegex(fp.ApiError, "20101"):
            c.login()

    def test_5xx_retries_then_succeeds_but_4xx_fails_fast(self):
        c, t = make({"/users/sign_in": [(503, {}), (503, {}), LOGIN]})
        c.login()
        self.assertEqual(len(t.requests), 3)
        c, t = make({"/users/sign_in": [(401, {})]})
        with self.assertRaisesRegex(fp.ApiError, "HTTP 401"):
            c.login()
        self.assertEqual(len(t.requests), 1)

    def test_network_errors_are_bounded(self):
        c, t = make({"/users/sign_in": [TimeoutError()]})
        with self.assertRaisesRegex(fp.ApiError, "TimeoutError.*3 attempts"):
            c.login()
        self.assertEqual(len(t.requests), 3)


class Auth(unittest.TestCase):
    def test_token_cached_private_reused_and_renewable(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "sub" / "token.json"
            c, t = make(cache=cache)
            c.login()
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
            c2, t2 = make(cache=cache)
            self.assertEqual(c2.login()["user_id"], "7")
            self.assertEqual(t2.requests, [])  # served from cache
            c2.login(renew=True)
            self.assertEqual(len(t2.requests), 1)

    def test_cache_for_another_account_or_near_expiry_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "token.json"
            cache.write_text(json.dumps({"email": "other@example.invalid", "token": "x",
                                         "user_info": {"user_id": "1"}, "expires_at": 9e12}))
            c, t = make(cache=cache)
            c.login()
            self.assertEqual(len(t.requests), 1)
            blob = json.loads(cache.read_text())
            blob["expires_at"] = __import__("time").time() + 3600
            cache.write_text(json.dumps(blob))
            c, t = make(cache=cache)
            c.login()
            self.assertEqual(len(t.requests), 1)

    def test_expired_token_relogs_in_once(self):
        c, t = make({"/goals/list_goal": [{"code": 403, "msg": "expired"}, ok({"goals": [1]})]})
        self.assertEqual(c.get("/goals/list_goal", user_id="7"), {"goals": [1]})
        self.assertEqual(sum(r.full_url.count("sign_in") for r in t.requests), 2)


class History(unittest.TestCase):
    def test_pages_merge_sort_and_apply_deletes(self):
        path = "/measurements/list_measurement"
        c, t = make({path: [page([row(2, 1_700_000_200), row(1, 1_700_000_100)], finish=0, lu="9", lm="2"),
                            page([row(3, 1_700_000_300)], finish=1, lu="10", lm="3", deleted=["2"])]})
        h = c.history("7")
        self.assertEqual([r["measurement_id"] for r in h["measurements"]], ["1", "3"])
        self.assertTrue(h["complete"])
        self.assertEqual((h["pages"], h["delete_measurement_ids"]), (2, ["2"]))
        self.assertEqual(t.query()["last_updated_at"], "9")  # second page used the returned cursor

    def test_stalled_cursor_unknown_flag_and_bad_schema_fail(self):
        path = "/measurements/list_measurement"
        c, _ = make({path: [page([], finish=0, lu="0", lm="0")]})
        with self.assertRaisesRegex(fp.ApiError, "stalled"):
            c.history("7")
        c, _ = make({path: [page([], finish=2)]})
        with self.assertRaisesRegex(fp.ApiError, "finish_flag"):
            c.history("7")
        c, _ = make({path: [ok({"measurements": "nope"})]})
        with self.assertRaisesRegex(fp.ApiError, "schema"):
            c.history("7")

    def test_snapshot_resumes_only_from_complete_history(self):
        path = "/measurements/list_measurement"
        old = {"measurements": [row(1, 1_700_000_100)], "count": 1, "complete": True,
               "last_updated_at": "5", "last_measurement_id": "1", "delete_measurement_ids": []}
        c, t = make({path: [page([row(1, 1_700_000_100), row(2, 1_700_000_200)], finish=1, lu="6", lm="2")]})
        snap = c.snapshot({"histories": {"7": old}})
        self.assertEqual(snap["histories"]["7"]["count"], 2)
        self.assertEqual(snap["profiles_resumed"], 1)
        self.assertEqual(t.measurement_queries()[0]["last_updated_at"], "5")
        c, t = make({path: [page([row(1, 1_700_000_100)], finish=1)]})
        c.snapshot({"histories": {"7": {**old, "complete": False}}})
        self.assertEqual(t.measurement_queries()[0]["last_updated_at"], "0")  # partial store is never trusted


class Store(unittest.TestCase):
    def test_load_rejects_missing_malformed_and_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            self.assertIsNone(store.load_store(p))
            p.write_text("{not json")
            self.assertIsNone(store.load_store(p))
            p.write_text(json.dumps({"complete": False}))
            self.assertIsNone(store.load_store(p))
            p.write_text(json.dumps({"complete": True}))
            self.assertEqual(store.load_store(p), {"complete": True})

    def test_save_is_private_and_reclaims_stale_lock(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            lock = Path(d) / "s.json.lock"
            lock.write_text("")
            old = __import__("time").time() - 600
            os.utime(lock, (old, old))
            store.save_store({"complete": True}, p)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertFalse(lock.exists())

    def test_rows_are_scoped_to_one_profile(self):
        blob = {"histories": {"7": {"measurements": [row(1, 1_700_000_100)]},
                              "8": {"measurements": [row(2, 1_700_000_200)]}}}
        self.assertEqual([r["measurement_id"] for r in store.rows(blob, "7")], ["1"])
        self.assertEqual(store.rows(blob, "9"), [])  # absent profile does not widen to all
        self.assertEqual(len(store.rows(blob)), 2)


class Report(unittest.TestCase):
    def test_timestamps_seconds_ms_and_garbage(self):
        self.assertEqual(report.timestamp({"time_stamp": 1_750_000_000}),
                         dt.datetime(2025, 6, 15, 15, 6, 40, tzinfo=dt.timezone.utc))
        self.assertEqual(report.timestamp({"time_stamp": 1_750_000_000_000}),
                         report.timestamp({"time_stamp": 1_750_000_000}))
        self.assertIsNone(report.timestamp({"time_stamp": 5}))
        self.assertIsNone(report.timestamp({}))

    def test_local_time_follows_dst_not_a_fixed_offset(self):
        tz = report.zone("America/New_York")
        summer = report.enrich(row(1, 1_750_000_000), tz=tz)
        winter = report.enrich(row(2, 1_740_000_000), tz=tz)
        self.assertTrue(summer["timestamp_local"].endswith("-04:00"))
        self.assertTrue(winter["timestamp_local"].endswith("-05:00"))
        self.assertTrue(summer["timestamp_utc"].endswith("+00:00"))

    def test_enrich_keeps_native_values_and_adds_pounds_only_on_request(self):
        r = row(1, 1_750_000_000, weight=68.0)
        self.assertNotIn("weight_lb", report.enrich(r))
        out = report.enrich(r, imperial=True)
        self.assertEqual((out["weight"], out["weight_lb"]), (68.0, 149.91))
        self.assertEqual(r, row(1, 1_750_000_000, weight=68.0))  # input untouched

    def test_daily_median_collapses_same_day_repeats(self):
        rows = [row(1, 1_750_000_000, 70), row(2, 1_750_000_100, 80), row(3, 1_750_000_200, 71)]
        self.assertEqual(report.daily_weights(rows), [(dt.date(2025, 6, 15), 71)])

    def test_summary_window_and_pounds(self):
        now = dt.datetime(2025, 6, 20, tzinfo=dt.timezone.utc)
        rows = [row(1, 1_749_000_000, 90), row(2, 1_750_000_000, 70, bodyfat=20),
                row(3, 1_750_100_000, 68, bodyfat=18)]
        s = report.summarize(rows, 14, imperial=True, now=now)
        self.assertEqual((s["total_measurements"], s["in_window"]), (3, 2))
        self.assertEqual((s["weight_latest"], s["weight_change"], s["body_fat_latest"]), (68, -2, 18))
        self.assertIn("weight_latest_lb", s)
        self.assertEqual(report.summarize([], 14, now=now)["in_window"], 0)

    def test_csv_has_all_columns_and_json_cells(self):
        text = report.csv_text([report.enrich(row(1, 1_750_000_000, extra_list=[1, 2])),
                                row(2, 1_750_000_100, only_here="x")])
        header = text.splitlines()[0].split(",")
        self.assertEqual(header[:2], ["timestamp_utc", "timestamp_local"])
        self.assertIn("only_here", header)
        self.assertIn('"[1, 2]"', text)


class Cli(unittest.TestCase):
    def run_cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_missing_credentials_exit_2_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"XDG_CONFIG_HOME": d}
            with mock.patch.dict(os.environ):
                os.environ.pop("FITPROFILE_EMAIL", None)
                os.environ.pop("FITPROFILE_PASSWORD", None)
                code, out, _ = self.run_cli("check", env=env)
        self.assertEqual(code, 2)
        self.assertIn("FITPROFILE_EMAIL", out)

    def test_env_file_is_parsed_without_expansion_and_real_env_wins(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "c.env"
            f.write_text("# c\nFITPROFILE_EMAIL=a@example.invalid\nFITPROFILE_PASSWORD='$(x) $HOME'\n")
            f.chmod(0o600)
            with mock.patch.dict(os.environ, {"FITPROFILE_EMAIL": "real@example.invalid"}):
                os.environ.pop("FITPROFILE_PASSWORD", None)
                cli.load_env_file(f)
                self.assertEqual(os.environ["FITPROFILE_PASSWORD"], "$(x) $HOME")
                self.assertEqual(os.environ["FITPROFILE_EMAIL"], "real@example.invalid")

    def test_loose_env_file_permissions_warn(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "c.env"
            f.write_text("X=1\n")
            f.chmod(0o644)
            with contextlib.redirect_stderr(io.StringIO()) as err, mock.patch.dict(os.environ):
                cli.load_env_file(f)
            self.assertIn("chmod 600", err.getvalue())

    def test_no_sync_summary_and_csv_read_only_the_store(self):
        blob = {"complete": True, "last_synced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "profiles": [{"user_id": "7", "primary": True}],
                "histories": {"7": {"measurements": [row(1, 1_750_000_000, 70, bodyfat=20)]}}}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(fp, "urlopen_transport", side_effect=AssertionError("network used")):
            s = Path(d) / "store.json"
            s.write_text(json.dumps(blob))
            code, out, _ = self.run_cli("--store", str(s), "--tz", "America/New_York",
                                        "summary", "--no-sync", "--imperial", "--days", "36500")
            data = json.loads(out)
            self.assertEqual((code, data["origin"], data["records"]), (0, "store", 1))
            self.assertEqual(data["latest"]["weight_lb"], 154.32)
            code, out, _ = self.run_cli("--store", str(s), "csv", "--no-sync", str(Path(d) / "o.csv"))
            self.assertEqual((code, json.loads(out)["rows"]), (0, 1))
            self.assertEqual(stat.S_IMODE((Path(d) / "o.csv").stat().st_mode), 0o600)

    def test_no_sync_without_store_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = self.run_cli("--store", str(Path(d) / "none.json"), "summary", "--no-sync")
        self.assertEqual(code, 2)
        self.assertIn("fitprofile sync", out)

    def test_failed_sync_falls_back_to_labelled_stale_store(self):
        blob = {"complete": True, "last_synced_at": "2025-01-01T00:00:00+00:00",
                "profiles": [{"user_id": "7", "primary": True}],
                "histories": {"7": {"measurements": [row(1, 1_750_000_000)]}}}
        c, _ = make({"/sub_users/list_sub_user": [(500, {})]})
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "make_client", return_value=c):
            s = Path(d) / "store.json"
            s.write_text(json.dumps(blob))
            code, out, _ = self.run_cli("--store", str(s), "json")
        data = json.loads(out)
        self.assertEqual((code, data["origin"], data["count"]), (0, "stale_store", 1))
        self.assertIn("sync failed", data["sync_note"])


if __name__ == "__main__":
    unittest.main()
