# Protocol notes

Observed behaviour of the Fit Profile cloud service, informed by static review of v1.34.0 of the Android app. Unofficial and subject to change. Statements below are observations, not server guarantees, unless stated otherwise.

## Requests

- Base URL: `https://fit-profile.qnclouds.com/api/v4`
- Query string: `app_id=fit_profile&locale=en`. The `app_id` must be exactly `fit_profile`; look-alike values (other brand names, different casing) do not authenticate this account.
- Bodies and responses are plain JSON. The official app always wraps them in an extra encryption layer (standard Base64, not URL-safe), but as observed the service currently accepts and returns plaintext, so this client does not use the wrapper. That could change without notice. If a response `data` field is ever a string instead of an object, the client stops with an error.
- Authenticated calls send `Authorization: Bearer <token>`.
- HTTP 200 does not mean success. Every response carries `code` (`200` on success) and `msg`. Treat anything else as an error. A `403` code means JWT authentication failed: expired or revoked token, or a badly skewed client clock.

## Login

`POST /users/sign_in` with `{"email": "...", "password": "<base64 RSA PKCS#1 v1.5 of the password>"}`. The RSA public key is the service's public key for receiving passwords. The response contains `token_info.token`, `token_info.remaining_time` (seconds; the server decides the lifetime, so use this value rather than assuming one) and `user_info` (including `user_id`). Cache the token rather than logging in on every run.

## Read endpoints (all GET)

Some destructive operations (for example deleting or toggling a health report) are also plain GETs, so the client allowlists exactly the paths below instead of trusting the HTTP method.

| Dataset | Path | Parameters |
|---|---|---|
| Primary profile | `/users/get_primary_user` | none (returns `user_info`) |
| Sub-profiles | `/sub_users/list_sub_user` | none |
| Measurements | `/measurements/list_measurement` | `user_id`, `last_updated_at`, `last_measurement_id` |
| Devices | `/device_binds/list_device_bind` | none |
| Goals | `/goals/list_goal` | `user_id` |
| Settings | `/user_settings/show_common_setting` | none |

An account can hold several profiles (the primary user plus sub-users). Always scope reads to one `user_id`.

## Measurement pagination

Start at `last_updated_at=0&last_measurement_id=0`. Each page returns `measurements`, `delete_measurement_ids`, `last_updated_at`, `last_measurement_id` and `finish_flag`. Request the next page with the returned cursor until `finish_flag` is `1`.

- Pages were observed to hold 10 records; there is no server-side date filter or page-size parameter. Pages are a strictly sequential chain.
- Fail on a repeated cursor (stall) or any `finish_flag` other than `0`/`1`; never return a truncated history as complete.
- De-duplicate by `measurement_id` (keep it as a string), remove every ID in `delete_measurement_ids`, and sort by `time_stamp`, not by server order or ID.
- Resuming from a stored cursor was observed to re-send the boundary record, so merge by union, not append.
- Cursors belong to one account; never resume them under another login. Tombstones (`delete_measurement_ids`) are accumulated permanently, which assumes the server never reuses an ID.

## Deliberately not read

The API also exposes integration credentials (for example third-party fitness-service tokens), health-report management and account-changing endpoints. This client never calls them.

## Record fields

`time_stamp` is epoch seconds (UTC). `weight` is in kg. Records carry about 90 fields: `bodyfat`, `bmi`, `bmr`, `bodyage`, `muscle`, `sinew`, `water`, `bone`, `visfat`, `subfat`, `protein`, `score`, `heart_rate`, mass fields, and per-segment (arm, leg, trunk) fat, muscle and resistance values. Units of the segmental fields are not documented; do not assume them. Zero can mean "unavailable".
