#!/usr/bin/env python3
"""weather_report のロジックをネットワークなしで検証する。"""

import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import weather_report as wr  # noqa: E402

CONFIG = {
    "location": {"name": "東京都千代田区", "latitude": 35.6895, "longitude": 139.6917,
                 "timezone": "Asia/Tokyo"},
    "timeline_hours": [6, 9, 12, 15, 18, 21],
    "thresholds": {"umbrella_precipitation_probability": 50, "umbrella_precipitation_mm": 1.0,
                   "strong_wind_speed_ms": 10.0, "strong_gust_ms": 15.0},
}


def sample_forecast() -> dict:
    hours = [f"2026-03-20T{h:02d}:00" for h in range(24)]
    return {
        "daily": {
            "time": ["2026-03-20"], "weather_code": [1],
            "temperature_2m_max": [19.4], "temperature_2m_min": [8.1],
            "apparent_temperature_max": [17.8], "apparent_temperature_min": [5.2],
            "precipitation_sum": [0.0], "precipitation_probability_max": [10],
            "wind_speed_10m_max": [8.3], "wind_gusts_10m_max": [16.2],
            "wind_direction_10m_dominant": [200],
            "sunrise": ["2026-03-20T05:44"], "sunset": ["2026-03-20T17:53"],
        },
        "hourly": {
            "time": hours,
            "weather_code": [1] * 24,
            "temperature_2m": [8 + i * 0.5 for i in range(24)],
            "apparent_temperature": [6 + i * 0.5 for i in range(24)],
            "relative_humidity_2m": [45] * 24,
            "precipitation_probability": [10] * 24,
            "precipitation": [0.0] * 24,
            "wind_speed_10m": [5.0] * 24,
            "wind_gusts_10m": [9.0] * 24,
            "wind_direction_10m": [200] * 24,
        },
    }


class WindAndWeatherTest(unittest.TestCase):
    def test_wind_direction_boundaries(self):
        self.assertEqual(wr.describe_wind_direction(0), "北")
        self.assertEqual(wr.describe_wind_direction(359), "北")
        self.assertEqual(wr.describe_wind_direction(90), "東")
        self.assertEqual(wr.describe_wind_direction(180), "南")
        self.assertEqual(wr.describe_wind_direction(270), "西")
        self.assertEqual(wr.describe_wind_direction(None), "不明")

    def test_unknown_weather_code_does_not_crash(self):
        self.assertIn("天気コード", wr.describe_weather(123))
        self.assertEqual(wr.describe_weather(None), "不明")


class PollenEstimateTest(unittest.TestCase):
    def test_half_month_index(self):
        self.assertEqual(wr.half_month_index(date(2026, 1, 1)), 0)
        self.assertEqual(wr.half_month_index(date(2026, 1, 16)), 1)
        self.assertEqual(wr.half_month_index(date(2026, 12, 31)), 23)

    def test_season_tables_cover_all_half_months(self):
        for name, table in wr.POLLEN_SEASON.items():
            self.assertEqual(len(table), 24, name)

    def test_cedar_peaks_in_early_march(self):
        peak = {p["name"]: p for p in wr.estimate_pollen(date(2026, 3, 5), 18.0, 40.0, 7.0, 0.0)}
        self.assertEqual(peak["スギ"]["label"], "非常に多い")
        self.assertEqual(peak["ブタクサ"]["level"], 0)

    def test_rain_suppresses_dispersal(self):
        dry = {p["name"]: p for p in wr.estimate_pollen(date(2026, 3, 5), 18.0, 40.0, 7.0, 0.0)}
        wet = {p["name"]: p for p in wr.estimate_pollen(date(2026, 3, 5), 12.0, 85.0, 1.0, 12.0)}
        self.assertLess(wet["スギ"]["level"], dry["スギ"]["level"])

    def test_ragweed_peaks_in_september(self):
        autumn = {p["name"]: p for p in wr.estimate_pollen(date(2026, 9, 19), 27.0, 55.0, 4.0, 0.0)}
        self.assertGreaterEqual(autumn["ブタクサ"]["level"], 4)
        self.assertEqual(autumn["ヒノキ"]["level"], 0)


class GooglePollenTest(unittest.TestCase):
    def test_reads_plant_level_index(self):
        payload = {"dailyInfo": [{"plantInfo": [
            {"code": "JAPANESE_CEDAR", "displayName": "スギ", "inSeason": True,
             "indexInfo": {"value": 4, "category": "High"}},
            {"code": "JAPANESE_CYPRESS", "displayName": "ヒノキ", "inSeason": False},
        ], "pollenTypeInfo": [
            {"code": "GRASS", "indexInfo": {"value": 2, "category": "Low"}},
            {"code": "WEED", "indexInfo": {"value": 1, "category": "Very Low"}},
        ]}]}
        result = {p["name"]: p for p in wr.extract_google_pollen(payload)}
        self.assertEqual(result["スギ"]["level"], 4)
        self.assertEqual(result["スギ"]["label"], "多い")
        # inSeason=false かつ index なしは「シーズン外＝0」として扱う
        self.assertEqual(result["ヒノキ"]["level"], 0)
        # plant 単位が無い種は大分類の指数で代替し、その旨を残す
        self.assertEqual(result["イネ科"]["level"], 2)
        self.assertIn("GRASS", result["イネ科"]["note"])

    def test_missing_data_is_reported_not_guessed(self):
        result = {p["name"]: p for p in wr.extract_google_pollen({"dailyInfo": [{}]})}
        self.assertIsNone(result["スギ"]["level"])
        self.assertEqual(result["スギ"]["label"], "データなし")


class ClothingTest(unittest.TestCase):
    def test_hot_day_suggests_short_sleeves_and_heat_warning(self):
        advice, notes = wr.clothing_advice(33.0, 26.0, 3.0, 6.0, 0, 0.0, CONFIG["thresholds"])
        self.assertIn("半袖", advice)
        self.assertTrue(any("熱中症" in n for n in notes))

    def test_rain_triggers_umbrella(self):
        _, notes = wr.clothing_advice(18.0, 12.0, 3.0, 6.0, 70, 5.0, CONFIG["thresholds"])
        self.assertTrue(any("傘" in n for n in notes))

    def test_rain_with_gale_suggests_rainwear_instead_of_umbrella(self):
        _, notes = wr.clothing_advice(18.0, 12.0, 12.0, 20.0, 80, 8.0, CONFIG["thresholds"])
        self.assertTrue(any("レインウェア" in n for n in notes))

    def test_large_daily_spread_is_called_out(self):
        _, notes = wr.clothing_advice(20.0, 6.0, 3.0, 6.0, 0, 0.0, CONFIG["thresholds"])
        self.assertTrue(any("重ね着" in n for n in notes))

    def test_missing_temperature_does_not_crash(self):
        advice, _ = wr.clothing_advice(None, None, None, None, None, None, CONFIG["thresholds"])
        self.assertIn("取得できません", advice)


class MarkdownTest(unittest.TestCase):
    def test_report_contains_every_requested_item(self):
        pollen = wr.estimate_pollen(date(2026, 3, 20), 19.4, 45.0, 8.3, 0.0)
        title, body = wr.build_markdown(CONFIG, sample_forecast(), pollen, "推定", "注記")
        self.assertIn("2026-03-20", title)
        for needle in ["気温", "体感気温", "湿度", "降水確率", "降水量", "風",
                       "スギ", "ヒノキ", "ブタクサ", "イネ科", "推奨服装", "時間別"]:
            self.assertIn(needle, body, needle)
        # 時間別は設定した6時刻ぶんの行が出る
        self.assertEqual(body.count("時 |"), len(CONFIG["timeline_hours"]))

    def test_empty_forecast_does_not_crash(self):
        title, body = wr.build_markdown(CONFIG, {}, [], "推定", "注記")
        self.assertIn("—", body)
        self.assertTrue(title)


class UnitsTest(unittest.TestCase):
    def test_forecast_request_pins_wind_speed_to_ms(self):
        """Open-Meteo の風速の既定は km/h。表示が m/s なので明示していないと値が 3.6 倍になる。"""
        captured = {}

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return {}

        original = wr.http_get_json
        wr.http_get_json = fake_get
        try:
            wr.fetch_forecast(35.6895, 139.6917, "Asia/Tokyo")
        finally:
            wr.http_get_json = original

        self.assertEqual(captured["params"]["wind_speed_unit"], "ms")
        self.assertEqual(captured["params"]["temperature_unit"], "celsius")
        self.assertEqual(captured["params"]["precipitation_unit"], "mm")


class DaytimePrecipitationTest(unittest.TestCase):
    @staticmethod
    def night_rain_hourly() -> dict:
        """深夜だけ強く降り、日中は降らない日。終日の最大値だけ見ると判断を誤る。"""
        hours = [f"2026-09-22T{h:02d}:00" for h in range(24)]
        probability = [94 if h < 5 else 10 for h in range(24)]
        amount = [6.0 if h < 5 else 0.0 for h in range(24)]
        return {"time": hours, "precipitation_probability": probability, "precipitation": amount}

    def test_night_rain_is_excluded_from_daytime(self):
        probability, amount = wr.daytime_precipitation(self.night_rain_hourly())
        self.assertEqual(probability, 10)
        self.assertEqual(amount, 0.0)

    def test_umbrella_advice_follows_daytime_not_all_day(self):
        forecast = sample_forecast()
        forecast["hourly"].update(self.night_rain_hourly())
        forecast["daily"]["precipitation_probability_max"] = [94]
        forecast["daily"]["precipitation_sum"] = [30.0]

        _, body = wr.build_markdown(CONFIG, forecast, [], "推定", "注記")
        self.assertIn("終日の最大 94%", body)
        self.assertIn("日中 6〜21時 10%", body)
        self.assertNotIn("傘を持って出る", body)

    def test_daytime_rain_still_triggers_umbrella(self):
        forecast = sample_forecast()
        forecast["hourly"]["precipitation_probability"] = [80] * 24
        forecast["hourly"]["precipitation"] = [3.0] * 24
        _, body = wr.build_markdown(CONFIG, forecast, [], "推定", "注記")
        self.assertIn("傘", body)

    def test_empty_hourly_returns_none(self):
        self.assertEqual(wr.daytime_precipitation({}), (None, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
