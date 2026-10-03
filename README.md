# fitprofile-cli

Unofficial, read-only command-line exporter for **Fit Profile** smart-scale data: weight, body fat, muscle, water, bone, BMR and the other bioimpedance (BIA) estimates your scale uploads. Tested with the **GE CS10G** scale and the Fit Profile Android app (v1.34.0, US region). Other QN-based scales that sync to the Fit Profile app may work; none are verified.

> **Not affiliated with, endorsed by, or supported by GE, Qingniu (QN), or the Fit Profile app.** All names are used only to describe compatibility. This talks to an undocumented service and can break without notice. It only reads data from your own account; it never writes, deletes, registers, or resets anything. Use at your own risk and check the service's terms for your situation.

## Install

```bash
pipx install git+https://github.com/Brandon168/fitprofile-cli   # or: pip install ...
```

Python 3.10+. The only dependency is `cryptography`.

## Set up credentials

Use the same email and password you use in the Fit Profile app. Either export them:

```bash
export FITPROFILE_EMAIL=you@example.com
export FITPROFILE_PASSWORD='your password'
```

or put them in `~/.config/fitprofile/credentials.env` (`chmod 600`; parsed as plain `KEY=value`, never shell-expanded):

```
FITPROFILE_EMAIL=you@example.com
FITPROFILE_PASSWORD=your password
```

Passwords are RSA-encrypted before they leave your machine and are never printed or placed in arguments.

## Use

```bash
fitprofile check                      # verify login
fitprofile summary --days 30 --imperial
fitprofile csv weights.csv            # every raw column, UTC + local timestamps
fitprofile json --imperial            # all measurements as JSON
fitprofile export snapshot.json       # profiles, histories, devices, goals, settings
fitprofile profiles | devices
```

Reads sync first, then answer from a local store, so they stay fast as history grows (the server returns 10 records per page, so a full pull is one request per 10 records; an update is one request). Useful flags:

| Flag | Effect |
|---|---|
| `--no-sync` | read the local store only, no network or credentials needed |
| `sync --full` | discard the cursor and re-pull everything |
| `--imperial` | add `weight_lb` beside the native kilograms |
| `--tz America/New_York` | local-time zone (default: system zone; or `FITPROFILE_TZ`) |
| `--store PATH` | store location (default `~/.local/share/fitprofile/store.json`) |

Every read reports `origin`: `synced`, `store`, or `stale_store` (sync failed, serving the last complete store, with a `sync_note`). Use `--store` to keep separate accounts apart.

## Data and privacy

Body-composition history is health data. The store, token cache (`~/.cache/fitprofile/token.json`) and any exports are written atomically with mode `0600`. Nothing is sent anywhere except the Fit Profile service. Native values are kept as the service reports them (weight in kg, timestamps in UTC); conversions are added alongside, never in place of them. Unit meanings of the many segmental fields are not guessed; zero may mean "not measured". BIA values are estimates, not clinical measurements.

## How it works

See [docs/protocol.md](docs/protocol.md) for the endpoints, cursor pagination and the guards against partial exports (stalled cursor, unknown completion flag, error codes that are not HTTP errors).

## Development

```bash
pip install -e . && python -m unittest discover -s tests -v
```

The tests are offline and use only synthetic fixtures.

## License

MIT. See [LICENSE](LICENSE).
