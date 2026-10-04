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

Some destructive operations (deleting or toggling a health report) are also plain GETs, so the client allowlists exactly the paths below instead of trusting the HTTP method. `/measurement/api/v4/...` paths are relative to the host root rather than `/api/v4`.

| Dataset | Path | Parameters |
|---|---|---|
| Unassigned readings | `/unknown_datas/list_unknown_data` | none (returns `unknown_datas`, `unknown_rope_records`, `unknown_blood_pressures`) |
| Girths (tape/derived circumferences) | `/girths/list_girth` | `user_id`, `last_updated_at`, `last_girth_id` (same cursor/`finish_flag` scheme as measurements) |
| Custom girth sites | `/girths/list_custom_girth` | `user_id` |
| Standalone heart rate | `/heart_rate_records/list_heart_rate_record` | `user_id`, `last_updated_at`, `last_heart_rate_record_id` (cursor scheme as above) |
| Monthly report availability | `/health_reports/list_health_report` | `user_id`, `record_date` (`YYYY-MM-DD`, month start; returns `present_flag`; `YYYY-MM` gives a 500). Observed on an account with no reports: it returns the previous month whatever date is sent |
| Monthly report | `/health_reports/show_health_report` | same; returns code `50000 Error Data` when no report exists |
| Weekly reports index | `/measurement/api/v4/measurement_weeklies/list_measurement_weekly` | `user_id`, `limit`, `page` (1-based; page 0 gives a 500) |
| Weekly report | `/measurement/api/v4/measurement_weeklies/show_measurement_weekly` | `user_id`, `week_day` (`YYYY-MM-DD`); daily series plus the week's last full measurement |
| Weight-goal report | `/measurement/api/v4/ai_weight_goal_reports/show_report` | `user_id`, `last_monday` |
| Body-fat calculations | `/bodyfat_calculations/list_bodyfat_calculation` | `user_id`, `last_updated_at`, `last_bodyfat_calculation_id` (cursor scheme as measurements) |
| Weight prediction | `/weight_predicts/list_weight_predict` | `user_id`, `zone` (IANA name; returns `history_weights`, `predict_weights`, `predict_days`, `weight_goal`) |
| Device users | `/device_users/list_device_user` | `mac` (from the device list) |
| Scale user slots | `/scale_users/list_scale_user` | `mac` |
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

## Write: delete a monthly report (untested)

`GET /health_reports/delete_health_report` is exposed as `fitprofile reports delete YYYY-MM-DD --yes`. Its parameters (`user_id`, `record_date`) are inferred from the matching read endpoints and it has **never been run against the live service**. `control_health_report_push` (a push-notification toggle) is not implemented: its parameters are unknown and it has little value for an exporter. Report generation endpoints (`generate_*`) are POSTs that persist state and are never called.

## Deliberately not called

The API also exposes integration credentials (for example third-party fitness-service tokens), report generation and account-changing endpoints. This client never calls them.

## Record fields

`time_stamp` is epoch seconds (UTC). `weight` is in kg. Records carry about 90 fields: `bodyfat`, `bmi`, `bmr`, `bodyage`, `muscle`, `sinew`, `water`, `bone`, `visfat`, `subfat`, `protein`, `score`, `heart_rate`, mass fields, and per-segment (arm, leg, trunk) fat, muscle and resistance values. Units of the segmental fields are not documented; do not assume them. Zero can mean "unavailable".

Parameter names for the secondary datasets above were discovered by probing (the service answers `422 Missing Params: <name>`), and were verified live on an account with no data in them, so record shapes for girths and heart rates are unverified. Device users, scale users, weight prediction and body-fat calculations were probed live: weight prediction and scale users returned data, the others were empty on this account.

## Known gaps

Not implemented, deliberately: AI measurement analysis (`ai_measurement_analysis/show_analysis` needs an `ai_measure_repo` value that was not established), report generation, report push toggle, friends/sharing, food/sport/step/water/blood-pressure data, and anything that returns integration credentials. Record shapes for girths, heart rates, body-fat calculations, and monthly/goal reports are unverified because those datasets are empty on the test account.
