#!/usr/bin/env python3
"""毎朝の天気レポート本文（Markdown）を組み立てる。

データ源:
  - 天気/気温/体感/湿度/降水/風: Open-Meteo Forecast API（APIキー不要）
  - 花粉: Google Pollen API（GOOGLE_POLLEN_API_KEY があるときだけ）
          キーが無い / 失敗したときは気象条件からの簡易推定にフォールバックする。

標準ライブラリだけで動かす（GitHub Actions 上で pip install を不要にするため）。
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any

FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
POLLEN_ENDPOINT = "https://pollen.googleapis.com/v1/forecast:lookup"
USER_AGENT = "daily-weather-notification/1.0 (+https://github.com/mobien666/Hello)"

# WMO Weather interpretation code (WW) -> (絵文字, 日本語)
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("☀️", "快晴"),
    1: ("🌤️", "晴れ"),
    2: ("⛅", "一部くもり"),
    3: ("☁️", "くもり"),
    45: ("🌫️", "霧"),
    48: ("🌫️", "霧氷の霧"),
    51: ("🌦️", "弱い霧雨"),
    53: ("🌦️", "霧雨"),
    55: ("🌧️", "強い霧雨"),
    56: ("🌧️", "着氷性の弱い霧雨"),
    57: ("🌧️", "着氷性の霧雨"),
    61: ("🌦️", "弱い雨"),
    63: ("🌧️", "雨"),
    65: ("🌧️", "強い雨"),
    66: ("🌧️", "着氷性の弱い雨"),
    67: ("🌧️", "着氷性の雨"),
    71: ("🌨️", "弱い雪"),
    73: ("🌨️", "雪"),
    75: ("❄️", "強い雪"),
    77: ("🌨️", "霧雪"),
    80: ("🌦️", "弱いにわか雨"),
    81: ("🌧️", "にわか雨"),
    82: ("⛈️", "激しいにわか雨"),
    85: ("🌨️", "にわか雪"),
    86: ("❄️", "強いにわか雪"),
    95: ("⛈️", "雷雨"),
    96: ("⛈️", "雹をともなう雷雨"),
    99: ("⛈️", "激しい雹をともなう雷雨"),
}

WIND_DIRECTIONS = [
    "北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
    "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西",
]


def now_in(timezone_name: str) -> datetime:
    """設定のタイムゾーンでの現在時刻。tzdata が無い環境ではシステム時刻に落とす。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(timezone_name))
    except Exception:
        return datetime.now()


def describe_weather(code: int | None) -> str:
    """WMO の天気コードを絵文字つきの日本語にする。未知のコードはそのまま返す。"""
    if code is None:
        return "不明"
    emoji, text = WMO_CODES.get(int(code), ("🌡️", f"天気コード {code}"))
    return f"{emoji} {text}"


def describe_wind_direction(degrees: float | None) -> str:
    """風向（度）を16方位の日本語にする。気象の慣例どおり「風が吹いてくる方角」。"""
    if degrees is None:
        return "不明"
    index = int((float(degrees) + 11.25) % 360 // 22.5)
    return WIND_DIRECTIONS[index]


def beaufort_note(speed_ms: float | None) -> str:
    """風速から体感の目安を返す。気象庁の風の強さの表現に沿った区分。"""
    if speed_ms is None:
        return ""
    if speed_ms < 3:
        return "穏やか"
    if speed_ms < 5:
        return "そよ風"
    if speed_ms < 10:
        return "やや強い風"
    if speed_ms < 15:
        return "強い風（歩きにくい）"
    if speed_ms < 20:
        return "非常に強い風（転倒注意）"
    return "猛烈な風（外出は危険）"


def http_get_json(url: str, params: dict[str, Any] | None = None,
                  headers: dict[str, str] | None = None,
                  retries: int = 3, timeout: int = 20) -> Any:
    """GET して JSON を返す。一時的な失敗は指数バックオフで再試行する。"""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    context = ssl.create_default_context()
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"GET に失敗しました: {url.split('?')[0]} ({last_error})")


def fetch_forecast(latitude: float, longitude: float, timezone: str) -> dict[str, Any]:
    """Open-Meteo から今日ぶんの予報を取得する。"""
    return http_get_json(FORECAST_ENDPOINT, {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "forecast_days": 1,
        # 既定の風速単位は km/h。表示は m/s なのでここで揃えておく。
        "wind_speed_unit": "ms",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "current": ",".join([
            "temperature_2m", "apparent_temperature", "relative_humidity_2m",
            "precipitation", "weather_code", "wind_speed_10m", "wind_direction_10m",
        ]),
        "hourly": ",".join([
            "temperature_2m", "apparent_temperature", "relative_humidity_2m",
            "precipitation_probability", "precipitation", "weather_code",
            "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
        ]),
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "apparent_temperature_max", "apparent_temperature_min",
            "precipitation_sum", "precipitation_probability_max",
            "wind_speed_10m_max", "wind_gusts_10m_max", "wind_direction_10m_dominant",
            "sunrise", "sunset",
        ]),
    })


def fetch_google_pollen(latitude: float, longitude: float, api_key: str) -> dict[str, Any]:
    """Google Pollen API から当日の花粉指数を取得する。

    日本ではスギ(JAPANESE_CEDAR)・ヒノキ(JAPANESE_CYPRESS)を含む樹木/イネ科/雑草に対応する。
    plant のコード体系は増えることがあるので、未知コードは API の displayName をそのまま使う。
    """
    return http_get_json(POLLEN_ENDPOINT, {
        "key": api_key,
        "location.latitude": latitude,
        "location.longitude": longitude,
        "days": 1,
        "languageCode": "ja",
        "plantsDescription": "false",
    })


# --- 花粉 -----------------------------------------------------------------

# Google Pollen API の plant コード -> 日本語名
TARGET_PLANTS: dict[str, str] = {
    "JAPANESE_CEDAR": "スギ",
    "JAPANESE_CYPRESS": "ヒノキ",
    "RAGWEED": "ブタクサ",
    "GRAMINALES": "イネ科",
}

# plant が返らなかったときに使う、大分類（pollenTypeInfo）での代替
PLANT_FALLBACK_TYPE: dict[str, str] = {
    "スギ": "TREE",
    "ヒノキ": "TREE",
    "ブタクサ": "WEED",
    "イネ科": "GRASS",
}

# Universal Pollen Index (0-5) -> 日本語ラベル
UPI_LABELS: dict[int, str] = {
    0: "なし",
    1: "非常に少ない",
    2: "少ない",
    3: "やや多い",
    4: "多い",
    5: "非常に多い",
}

# 半月ごと（1月前半=0 … 12月後半=23）の飛散しやすさ。関東の一般的な飛散カレンダーを要約した近似値で、
# 実測ではない。推定モードでのみ使う。
POLLEN_SEASON: dict[str, list[float]] = {
    "スギ":     [0.05, 0.20, 0.80, 1.00, 1.00, 0.70, 0.30, 0.10, 0.0, 0.0, 0.0, 0.0,
                 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.02],
    "ヒノキ":   [0.0, 0.0, 0.0, 0.05, 0.10, 0.50, 1.00, 0.80, 0.40, 0.10, 0.0, 0.0,
                 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "イネ科":   [0.0, 0.0, 0.0, 0.0, 0.05, 0.10, 0.30, 0.50, 0.80, 1.00, 1.00, 0.80,
                 0.50, 0.40, 0.40, 0.50, 0.70, 0.70, 0.50, 0.30, 0.10, 0.0, 0.0, 0.0],
    "ブタクサ": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                 0.0, 0.10, 0.30, 0.60, 1.00, 1.00, 0.60, 0.30, 0.10, 0.0, 0.0, 0.0],
}


def half_month_index(day: date) -> int:
    """日付を半月インデックス（1月前半=0 … 12月後半=23）に落とす。"""
    return (day.month - 1) * 2 + (0 if day.day <= 15 else 1)


def estimate_pollen(day: date, temp_max: float | None, humidity_mean: float | None,
                    wind_max: float | None, precipitation: float | None) -> list[dict[str, Any]]:
    """気象条件から花粉の飛散しやすさを推定する（実測値ではない）。

    飛散カレンダーの季節係数に、「気温が高い・湿度が低い・風が強い・雨が降らない日ほど飛びやすい」
    という一般的な経験則を気象係数として掛け合わせただけの簡易モデル。
    """
    weather_factor = 1.0
    if precipitation is not None:
        if precipitation >= 5:
            weather_factor *= 0.2
        elif precipitation >= 1:
            weather_factor *= 0.5
    if temp_max is not None:
        if temp_max >= 18:
            weather_factor *= 1.2
        elif temp_max < 10:
            weather_factor *= 0.7
    if humidity_mean is not None:
        if humidity_mean < 40:
            weather_factor *= 1.2
        elif humidity_mean >= 70:
            weather_factor *= 0.7
    if wind_max is not None:
        if wind_max >= 7:
            weather_factor *= 1.2
        elif wind_max < 2:
            weather_factor *= 0.85

    index = half_month_index(day)
    results = []
    for name, calendar in POLLEN_SEASON.items():
        score = calendar[index] * weather_factor
        if score >= 0.80:
            level, label = 5, "非常に多い"
        elif score >= 0.55:
            level, label = 4, "多い"
        elif score >= 0.30:
            level, label = 3, "やや多い"
        elif score >= 0.10:
            level, label = 2, "少ない"
        elif score > 0:
            level, label = 1, "非常に少ない"
        else:
            level, label = 0, "ほぼなし（時期外）"
        results.append({"name": name, "level": level, "label": label, "note": ""})
    return results


def extract_google_pollen(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Google Pollen API のレスポンスから対象4種を取り出す。

    plant 単位の情報が無い種は、大分類（樹木/イネ科/雑草）の指数で代替し、その旨を note に残す。
    """
    daily = (payload.get("dailyInfo") or [{}])[0]
    plants = {item.get("code"): item for item in daily.get("plantInfo") or []}
    types = {item.get("code"): item for item in daily.get("pollenTypeInfo") or []}

    def read_index(info: dict[str, Any] | None) -> tuple[int | None, str]:
        if not info:
            return None, ""
        index_info = info.get("indexInfo") or {}
        value = index_info.get("value")
        if value is None:
            return (0, "") if info.get("inSeason") is False else (None, "")
        return int(value), str(index_info.get("category") or "")

    results = []
    for code, name in TARGET_PLANTS.items():
        level, category = read_index(plants.get(code))
        note = ""
        if level is None:
            fallback_code = PLANT_FALLBACK_TYPE[name]
            level, category = read_index(types.get(fallback_code))
            if level is not None:
                note = f"{fallback_code} 全体の指数で代替"
        if level is None:
            results.append({"name": name, "level": None, "label": "データなし", "note": ""})
            continue
        label = UPI_LABELS.get(level, category or str(level))
        results.append({"name": name, "level": level, "label": label, "note": note})
    return results


# --- 服装 -----------------------------------------------------------------

# 体感気温（日中の最高）に対する服装の目安。気象情報サイトで一般的に使われる5℃刻みの区分。
CLOTHING_TABLE: list[tuple[float, str]] = [
    (30.0, "👕 半袖＋通気性の良い素材。日傘・帽子と水分をこまめに"),
    (25.0, "👕 半袖シャツ1枚で快適。冷房対策に薄手の羽織りを1枚"),
    (20.0, "👔 長袖シャツ、または半袖＋薄手のカーディガン"),
    (15.0, "🧥 長袖＋薄手のジャケットやパーカー"),
    (10.0, "🧥 ニット＋ジャケット、トレンチコート"),
    (5.0, "🧣 厚手のコート＋ニット。マフラーがあると安心"),
    (0.0, "🧤 ダウンやウールコート＋マフラー・手袋"),
    (-99.0, "🥶 本格的な防寒装備。重ね着＋手袋・帽子・厚手の靴下"),
]


def clothing_advice(feels_max: float | None, feels_min: float | None,
                    wind_max: float | None, gust_max: float | None,
                    precipitation_probability: float | None, precipitation_sum: float | None,
                    thresholds: dict[str, Any]) -> tuple[str, list[str]]:
    """体感気温・風・降水から服装の目安と注意書きを組み立てる。"""
    base = "🌡️ 気温データが取得できませんでした"
    if feels_max is not None:
        for floor, advice in CLOTHING_TABLE:
            if feels_max >= floor:
                base = advice
                break

    notes: list[str] = []
    if feels_max is not None and feels_min is not None:
        spread = feels_max - feels_min
        if spread >= 10:
            notes.append(f"朝晩と日中の体感差が {spread:.1f}℃ と大きめ。脱ぎ着できる重ね着が正解")
        elif spread >= 7:
            notes.append(f"体感の日較差 {spread:.1f}℃。羽織りものを1枚持っておくと安心")

    rain_by_probability = (precipitation_probability is not None
                           and precipitation_probability >= thresholds["umbrella_precipitation_probability"])
    rain_by_amount = (precipitation_sum is not None
                      and precipitation_sum >= thresholds["umbrella_precipitation_mm"])
    if rain_by_probability or rain_by_amount:
        if gust_max is not None and gust_max >= thresholds["strong_gust_ms"]:
            notes.append("☔ 雨＋強風。傘は壊れやすいのでレインウェアや撥水アウターが無難")
        else:
            notes.append("☔ 傘を持って出るのがおすすめ")
    elif precipitation_probability is not None and precipitation_probability >= 30:
        notes.append("🌂 にわか雨の可能性あり。折りたたみ傘があると安心")

    if wind_max is not None and wind_max >= thresholds["strong_wind_speed_ms"]:
        notes.append(f"💨 最大風速 {wind_max:.1f} m/s。髪型・帽子・スカートは風対策を")
    if feels_max is not None and feels_max >= 31:
        notes.append("🥵 熱中症に注意。水分と塩分、日陰での休憩を意識して")
    if feels_min is not None and feels_min <= 3:
        notes.append("🥶 路面凍結・冷え込みに注意。手袋と防寒インナーを")
    return base, notes


# --- 本文の組み立て -------------------------------------------------------

def hourly_value(hourly: dict[str, Any], key: str, index: int) -> Any:
    values = hourly.get(key) or []
    if 0 <= index < len(values):
        return values[index]
    return None


def mean(values: list[Any]) -> float | None:
    numbers = [v for v in values if isinstance(v, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


def format_number(value: Any, unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}{unit}"


def daytime_precipitation(hourly: dict[str, Any], start_hour: int = 6,
                          end_hour: int = 21) -> tuple[float | None, float | None]:
    """日中（既定 6〜21 時）の降水確率の最大と降水量の合計。

    終日の最大値は深夜の雨に引きずられることがあり、朝の身支度の判断材料にならない。
    """
    times: list[str] = hourly.get("time") or []
    probabilities: list[float] = []
    amounts: list[float] = []
    for index, stamp in enumerate(times):
        try:
            hour = datetime.fromisoformat(stamp).hour
        except ValueError:
            continue
        if not start_hour <= hour <= end_hour:
            continue
        probability = hourly_value(hourly, "precipitation_probability", index)
        if isinstance(probability, (int, float)):
            probabilities.append(float(probability))
        amount = hourly_value(hourly, "precipitation", index)
        if isinstance(amount, (int, float)):
            amounts.append(float(amount))
    return (max(probabilities) if probabilities else None,
            sum(amounts) if amounts else None)


def build_timeline_rows(hourly: dict[str, Any], wanted_hours: list[int]) -> list[list[str]]:
    """3時間おきなど、指定した時刻の行だけを抜き出す。"""
    times: list[str] = hourly.get("time") or []
    rows = []
    for index, stamp in enumerate(times):
        try:
            hour = datetime.fromisoformat(stamp).hour
        except ValueError:
            continue
        if hour not in wanted_hours:
            continue
        rows.append([
            f"{hour:02d}時",
            describe_weather(hourly_value(hourly, "weather_code", index)),
            format_number(hourly_value(hourly, "temperature_2m", index), "℃"),
            format_number(hourly_value(hourly, "apparent_temperature", index), "℃"),
            format_number(hourly_value(hourly, "relative_humidity_2m", index), "%", 0),
            format_number(hourly_value(hourly, "precipitation_probability", index), "%", 0),
            (f"{describe_wind_direction(hourly_value(hourly, 'wind_direction_10m', index))} "
             f"{format_number(hourly_value(hourly, 'wind_speed_10m', index), ' m/s')}"),
        ])
    return rows


def build_markdown(config: dict[str, Any], forecast: dict[str, Any],
                   pollen: list[dict[str, Any]], pollen_source: str,
                   pollen_note: str) -> tuple[str, str]:
    """通知本文（Markdown）とタイトルを組み立てる。"""
    location = config["location"]
    thresholds = config["thresholds"]
    daily = forecast.get("daily") or {}
    hourly = forecast.get("hourly") or {}

    def daily_value(key: str) -> Any:
        values = daily.get(key) or []
        return values[0] if values else None

    today_text = daily_value("time") or date.today().isoformat()
    try:
        today = date.fromisoformat(str(today_text))
    except ValueError:
        today = date.today()
    weekday = "月火水木金土日"[today.weekday()]

    weather_code = daily_value("weather_code")
    temp_max, temp_min = daily_value("temperature_2m_max"), daily_value("temperature_2m_min")
    feels_max, feels_min = daily_value("apparent_temperature_max"), daily_value("apparent_temperature_min")
    precipitation_sum = daily_value("precipitation_sum")
    precipitation_probability = daily_value("precipitation_probability_max")
    wind_max, gust_max = daily_value("wind_speed_10m_max"), daily_value("wind_gusts_10m_max")
    wind_direction = daily_value("wind_direction_10m_dominant")
    humidity_mean = mean(hourly.get("relative_humidity_2m") or [])

    day_probability, day_precipitation = daytime_precipitation(hourly)
    advice, notes = clothing_advice(
        feels_max, feels_min, wind_max, gust_max,
        day_probability if day_probability is not None else precipitation_probability,
        day_precipitation if day_precipitation is not None else precipitation_sum,
        thresholds)

    title = (f"☀️ {today.isoformat()}({weekday}) {location['name']}の天気 — "
             f"{describe_weather(weather_code).split(' ', 1)[-1]} "
             f"{format_number(temp_max, '℃', 0)}/{format_number(temp_min, '℃', 0)} "
             f"降水{format_number(precipitation_probability, '%', 0)}")

    lines: list[str] = []
    lines.append(f"## {describe_weather(weather_code)}　{today.isoformat()}（{weekday}）")
    lines.append("")
    sunrise = str(daily_value("sunrise") or "")[-5:] or "—"
    sunset = str(daily_value("sunset") or "")[-5:] or "—"
    lines.append(f"**{location['name']}**　🌅 {sunrise}　🌇 {sunset}")
    lines.append("")
    lines.append("### 📊 今日の概況")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("| --- | --- |")
    lines.append(f"| 🌡️ 気温 | 最高 {format_number(temp_max, '℃')} / 最低 {format_number(temp_min, '℃')} |")
    lines.append(f"| 🤔 体感気温 | 最高 {format_number(feels_max, '℃')} / 最低 {format_number(feels_min, '℃')} |")
    lines.append(f"| 💧 湿度 | 平均 {format_number(humidity_mean, '%', 0)} |")
    lines.append(f"| ☔ 降水確率 | 終日の最大 {format_number(precipitation_probability, '%', 0)}"
                 f"（日中 6〜21時 {format_number(day_probability, '%', 0)}） |")
    lines.append(f"| 🌧️ 降水量 | 終日 合計 {format_number(precipitation_sum, ' mm')}"
                 f"（日中 {format_number(day_precipitation, ' mm')}） |")
    lines.append(f"| 💨 風 | {describe_wind_direction(wind_direction)}の風 "
                 f"最大 {format_number(wind_max, ' m/s')}（瞬間 {format_number(gust_max, ' m/s')}）"
                 f"{'・' + beaufort_note(wind_max) if beaufort_note(wind_max) else ''} |")
    lines.append("")

    timeline = build_timeline_rows(hourly, config.get("timeline_hours") or [])
    if timeline:
        lines.append("### ⏰ 時間別")
        lines.append("")
        lines.append("| 時刻 | 天気 | 気温 | 体感 | 湿度 | 降水確率 | 風 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in timeline:
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    lines.append("### 🌾 花粉")
    lines.append("")
    lines.append("| 種類 | 飛散レベル |")
    lines.append("| --- | --- |")
    for item in pollen:
        level = item["level"]
        gauge = "●" * level + "○" * (5 - level) if isinstance(level, int) else "—"
        suffix = f"（{item['note']}）" if item["note"] else ""
        lines.append(f"| {item['name']} | {gauge} {item['label']}{suffix} |")
    lines.append("")
    lines.append(f"> {pollen_note}")
    lines.append("")

    lines.append("### 👕 推奨服装")
    lines.append("")
    lines.append(advice)
    if notes:
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.append("---")
    lines.append("")
    generated_at = now_in(location.get("timezone", "Asia/Tokyo"))
    lines.append(f"<sub>天気: [Open-Meteo](https://open-meteo.com/)（無料・非商用ライセンス） / "
                 f"花粉: {pollen_source} / "
                 f"生成: {generated_at.strftime('%Y-%m-%d %H:%M')} "
                 f"{location.get('timezone', 'Asia/Tokyo')}</sub>")
    return title, "\n".join(line for line in lines if line is not None)


# --- エントリポイント -----------------------------------------------------

def load_config(path: str) -> dict[str, Any]:
    """設定ファイルを読み、環境変数があれば地点だけ上書きする。"""
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    location = config["location"]
    if os.environ.get("WEATHER_LATITUDE"):
        location["latitude"] = float(os.environ["WEATHER_LATITUDE"])
    if os.environ.get("WEATHER_LONGITUDE"):
        location["longitude"] = float(os.environ["WEATHER_LONGITUDE"])
    if os.environ.get("WEATHER_LOCATION_NAME"):
        location["name"] = os.environ["WEATHER_LOCATION_NAME"]
    return config


def resolve_pollen(config: dict[str, Any], forecast: dict[str, Any],
                   api_key: str | None) -> tuple[list[dict[str, Any]], str, str]:
    """花粉情報を返す。Google Pollen API が使えないときは推定にフォールバックする。"""
    location = config["location"]
    if api_key:
        try:
            payload = fetch_google_pollen(location["latitude"], location["longitude"], api_key)
            return (
                extract_google_pollen(payload),
                "[Google Pollen API](https://developers.google.com/maps/documentation/pollen)",
                "Google Pollen API の実データ（Universal Pollen Index 0〜5）です。",
            )
        except Exception as error:  # 花粉が取れなくても天気は届けたいので握りつぶす
            print(f"::warning::Google Pollen API の取得に失敗したため推定にフォールバックします: {error}",
                  file=sys.stderr)

    daily = forecast.get("daily") or {}
    hourly = forecast.get("hourly") or {}

    def first(key: str) -> Any:
        values = daily.get(key) or []
        return values[0] if values else None

    try:
        today = date.fromisoformat(str((daily.get("time") or [""])[0]))
    except (ValueError, IndexError):
        today = date.today()

    estimated = estimate_pollen(
        today,
        first("temperature_2m_max"),
        mean(hourly.get("relative_humidity_2m") or []),
        first("wind_speed_10m_max"),
        first("precipitation_sum"),
    )
    reason = ("APIキー未設定のため" if not api_key else "Google Pollen API に接続できなかったため")
    return (
        estimated,
        "飛散カレンダー＋気象条件による推定",
        f"⚠️ **これは実測ではなく推定値です**（{reason}）。"
        "飛散時期の目安に、気温・湿度・風・降水から算出した簡易スコアを掛けたものです。"
        "実データが必要な場合は `GOOGLE_POLLEN_API_KEY` を設定してください。",
    )


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="毎朝の天気レポートを Markdown で出力する")
    parser.add_argument("--config", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "config.json"))
    parser.add_argument("--output", help="本文の書き出し先ファイル")
    parser.add_argument("--title-output", help="タイトルの書き出し先ファイル")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    location = config["location"]
    forecast = fetch_forecast(location["latitude"], location["longitude"], location["timezone"])
    pollen, source, note = resolve_pollen(config, forecast, os.environ.get("GOOGLE_POLLEN_API_KEY"))
    title, body = build_markdown(config, forecast, pollen, source, note)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(body + "\n")
    if args.title_output:
        with open(args.title_output, "w", encoding="utf-8") as handle:
            handle.write(title)
    if not args.output:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
