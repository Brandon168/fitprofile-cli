# Protocol notes

Observed behaviour of the Fit Profile cloud service as of v1.34.0 of the Android app. Unofficial and subject to change; verify before relying on it.

## Requests

- Base URL: `https://fit-profile.qnclouds.com/api/v4`
- Query string: `app_id=fit_profile&locale=en`. The `app_id` must be exactly `fit_profile`; look-alike values (other brand names, different casing) do not authenticate this account.
- Bodies and responses are plain JSON. The official app wraps them in an extra encryption layer, but the service currently accepts and returns plaintext, so this client does not use it. If a response `data` field is ever a string instead of an object, the protocol has changed and the client stops with an error.
- Authenticated calls send `Authorization: Bearer <token>`.
- HTTP 200 does not mean success. Every response carries `code` (`200` or `0` on success) and `msg`. Treat anything else, including a `403` code inside a 200 response (expired token), as an error.

## Login

`POST /users/sign_in` with `{"email": "...", "password": "<base64 RSA PKCS#1 v1.5 of the password>"}`. The RSA public key is the service's public key for receiving passwords. The response contains `token_info.token`, `token_info.remaining_time` (seconds; about 180 days) and `user_info` (including `user_id`). Cache the token rather than logging in on every run.

## Read endpoints (all GET)

| Dataset | Path | Parameters |
|---|---|---|
| Sub-profiles | `/sub_users/list_sub_user` | none |
| Measurements | `/measurements/list_measurement` | `user_id`, `last_updated_at`, `last_measurement_id` |
| Devices | `/device_binds/list_device_bind` | none |
| Goals | `/goals/list_goal` | `user_id` |
| Settings | `/user_settings/show_common_setting` | none |

An account can hold several profiles (the primary user plus sub-users). Always scope reads to one `user_id`.

## Measurement pagination

Start at `last_updated_at=0&last_measurement_id=0`. Each page returns `measurements`, `delete_measurement_ids`, `last_updated_at`, `last_measurement_id` and `finish_flag`. Request the next page with the returned cursor until `finish_flag` is `1`.

- Pages are capped at 10 records; there is no server-side date filter or page-size parameter. Pages are a strictly sequential chain.
- Fail on a repeated cursor (stall) or any `finish_flag` other than `0`/`1`; never return a truncated history as complete.
- De-duplicate by `measurement_id` (keep it as a string), remove every ID in `delete_measurement_ids`, and sort by `time_stamp`, not by server order or ID.
- Resuming from a stored cursor re-sends the boundary record, so merge by union, not append.

## Record fields

`time_stamp` is epoch seconds (UTC). `weight` is in kg. Records carry about 90 fields: `bodyfat`, `bmi`, `bmr`, `bodyage`, `muscle`, `sinew`, `water`, `bone`, `visfat`, `subfat`, `protein`, `score`, `heart_rate`, mass fields, and per-segment (arm, leg, trunk) fat, muscle and resistance values. Units of the segmental fields are not documented; do not assume them. Zero can mean "unavailable".
