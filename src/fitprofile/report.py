"""Pure helpers: timestamps, units, summaries and CSV. No network, no disk."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import statistics
from typing import Any
from zoneinfo import ZoneInfo

KG_PER_LB = 0.45359237
UTC = dt.timezone.utc
EPOCH_MIN = dt.datetime.min.replace(tzinfo=UTC)
# Fields shown for the latest measurement; the full record is always available via json/csv.
HEADLINE = ["weight", "bmi", "bodyfat", "muscle", "water", "bone", "visfat", "protein", "bmr"]


def num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def timestamp(row: dict) -> dt.datetime | None:
    """Measurement time (UTC) from the record's epoch `time_stamp` (seconds or ms)."""
    n = num(row.get("time_stamp"))
    if n is None:
        return None
    if n > 1e11:
        n /= 1000
    return dt.datetime.fromtimestamp(n, UTC) if n > 1e9 else None


def sort_key(row: dict) -> tuple:
    return (timestamp(row) or EPOCH_MIN, str(row.get("measurement_id", "")))


def zone(name: str | None) -> ZoneInfo | None:
    """Named zone, or None for the system's local zone."""
    return ZoneInfo(name) if name else None


def enrich(row: dict, *, imperial: bool = False, tz: ZoneInfo | None = None) -> dict:
    """Add UTC/local timestamps (and pounds) beside the native values; nothing is overwritten."""
    out = dict(row)
    when = timestamp(row)
    if when:
        out["timestamp_utc"] = when.isoformat()
        out["timestamp_local"] = when.astimezone(tz).isoformat()
    weight = num(row.get("weight"))
    if imperial and weight is not None:
        out["weight_lb"] = round(weight / KG_PER_LB, 2)
    return out


def daily_weights(rows: list[dict]) -> list[tuple[dt.date, float]]:
    """One weight per UTC day (median, since same-day repeats happen)."""
    by_day: dict[dt.date, list[float]] = {}
    for row in rows:
        when, weight = timestamp(row), num(row.get("weight"))
        if when and weight is not None:
            by_day.setdefault(when.date(), []).append(weight)
    return [(day, statistics.median(v)) for day, v in sorted(by_day.items())]


def summarize(rows: list[dict], days: int, *, imperial: bool = False,
              tz: ZoneInfo | None = None, now: dt.datetime | None = None) -> dict:
    cutoff = (now or dt.datetime.now(UTC)) - dt.timedelta(days=days)
    recent = sorted((r for r in rows if (timestamp(r) or EPOCH_MIN) >= cutoff), key=sort_key)
    out: dict[str, Any] = {"total_measurements": len(rows), "in_window": len(recent), "window_days": days}
    weights = [(timestamp(r), num(r.get("weight"))) for r in recent]
    weights = [(t, w) for t, w in weights if t and w is not None]
    if weights:
        values = [w for _, w in weights]
        out.update(first_in_window=weights[0][0].astimezone(tz).isoformat(),
                   last_in_window=weights[-1][0].astimezone(tz).isoformat(),
                   weight_latest=values[-1], weight_min=min(values), weight_max=max(values),
                   weight_avg=round(sum(values) / len(values), 2),
                   weight_change=round(values[-1] - values[0], 2))
        if imperial:
            for key in ("weight_latest", "weight_min", "weight_max", "weight_avg", "weight_change"):
                out[key + "_lb"] = round(out[key] / KG_PER_LB, 2)
    fat = [f for f in (num(r.get("bodyfat")) for r in recent) if f is not None]
    if fat:
        out.update(body_fat_latest=fat[-1], body_fat_avg=round(sum(fat) / len(fat), 2),
                   body_fat_min=min(fat), body_fat_max=max(fat))
    return out


def csv_text(rows: list[dict]) -> str:
    """Every raw column (nested values as JSON), timestamp columns first."""
    flat = [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
             for k, v in r.items()} for r in rows]
    lead = [c for c in ("timestamp_utc", "timestamp_local") if any(c in r for r in flat)]
    columns = lead + sorted({k for r in flat for k in r} - set(lead))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(flat)
    return stream.getvalue()
