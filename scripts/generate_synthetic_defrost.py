from __future__ import annotations

import csv
import math
from datetime import datetime, timedelta
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    REPOSITORY_ROOT
    / "examples"
    / "synthetic_hvac"
    / "datasets"
    / "raw"
    / "defrost_telemetry.csv"
)


def command_state(timestamp: datetime) -> tuple[str, int, int, int, float, str]:
    seconds = timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second
    if 3 * 3600 + 59 * 60 + 30 <= seconds < 4 * 3600:
        return "heating", 1, 0, 0, -2.5, ""
    if 4 * 3600 <= seconds < 4 * 3600 + 240:
        progress = (seconds - 4 * 3600) / 240
        return "defrost", 0, 1, 1, -2.0 + 9.0 * progress, ""
    if 4 * 3600 + 240 <= seconds < 4 * 3600 + 260:
        return "recovery", 0, 0, 0, 7.0, ""
    if 15 * 3600 + 58 * 60 <= seconds < 16 * 3600:
        return "heating", 1, 0, 0, 8.0, ""
    if 16 * 3600 <= seconds < 16 * 3600 + 400:
        alarm = "DF_MAX_TIME" if seconds > 16 * 3600 + 300 else ""
        return "defrost", 1, 1, 1, 8.0, alarm
    if 16 * 3600 + 400 <= seconds < 16 * 3600 + 420:
        return "recovery", 0, 0, 0, 8.0, "DF_MAX_TIME"
    return "heating", 1, 0, 0, float("nan"), ""


def generate() -> int:
    start = datetime(2026, 7, 15)
    rows: list[dict[str, object]] = []
    for index in range(24 * 60 * 6):
        timestamp = start + timedelta(seconds=index * 10)
        day_fraction = index / (24 * 60 * 6)
        outdoor = 4.0 + 6.0 * math.sin(2 * math.pi * (day_fraction - 0.25))
        mode, fan, valve, defrost, coil_override, alarm = command_state(timestamp)
        coil = outdoor + 3.0 if math.isnan(coil_override) else coil_override
        compressor = 1
        suction_pressure = 430.0 + 3.0 * math.sin(index / 80)
        discharge_pressure = 1680.0 + 25.0 * math.cos(index / 120)
        if defrost:
            suction_pressure += 18.0
            discharge_pressure -= 35.0
        suction_temp = coil + 4.0
        discharge_temp = 74.0 + 2.0 * math.sin(index / 90)
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "asset_id": "HP-01",
                "mode": mode,
                "outdoor_temp_c": round(outdoor, 3),
                "outdoor_coil_temp_c": round(coil, 3),
                "suction_pressure_kpa": round(suction_pressure, 3),
                "discharge_pressure_kpa": round(discharge_pressure, 3),
                "suction_temp_c": round(suction_temp, 3),
                "discharge_temp_c": round(discharge_temp, 3),
                "superheat_k": 4.0,
                "subcooling_k": 5.0,
                "compressor_command": compressor,
                "outdoor_fan_command": fan,
                "reversing_valve_command": valve,
                "defrost_command": defrost,
                "alarm_code": alarm,
                "data_quality": "good",
            }
        )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    print(f"wrote {generate()} fully synthetic rows to {OUTPUT}")
