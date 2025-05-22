from flask import Flask, request
import datetime
import requests
import os
import json
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from opencc import OpenCC

load_dotenv()
app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")
CALENDAR_ID = os.getenv("CALENDAR_ID") or "primary"
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def get_calendar_service():
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def get_target_date():
    taiwan_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today = taiwan_now.date()
    weekday = today.weekday()
    return today + datetime.timedelta(days=3) if weekday == 4 else today + datetime.timedelta(days=1)

def get_google_calendar_events():
    service = get_calendar_service()
    target_date = get_target_date()
    start = datetime.datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0) - datetime.timedelta(hours=8)
    end = datetime.datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59) - datetime.timedelta(hours=8)
    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=start.isoformat() + 'Z',
        timeMax=end.isoformat() + 'Z',
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    return events_result.get('items', []), target_date

def geocode_location(location):
    try:
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": location,
            "key": GOOGLE_MAPS_API_KEY,
            "region": "tw",
            "language": "zh-TW"
        }
        res = requests.get(url, params=params, timeout=5)
        results = res.json().get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except Exception as e:
        print("地點查詢失敗：", e)
    return None

def get_township_from_coords(lat, lon):
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "latlng": f"{lat},{lon}",
            "key": GOOGLE_MAPS_API_KEY,
            "language": "zh-TW"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if data["status"] == "OK":
            for comp in data["results"][0]["address_components"]:
                if "administrative_area_level_3" in comp["types"]:
                    return comp["long_name"]
    except Exception as e:
        print("❌ 解析行政區失敗：", e)
    return None

def interpret_uv_index(uvi):
    try:
        uvi = float(uvi)
        if uvi <= 2: return "🟢 低"
        elif uvi <= 5: return "🟡 中等"
        elif uvi <= 7: return "🟠 高"
        elif uvi <= 10: return "🔴 很高"
        else: return "🟣 極高"
    except:
        return "❓ 未知"

def fetch_weather_by_weatherapi(location_name, day_offset, event_hour=None):
    try:
        url = "https://api.weatherapi.com/v1/forecast.json"
        params = {
            "key": WEATHERAPI_KEY,
            "q": location_name,
            "days": day_offset + 1,
            "lang": "zh"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if "forecast" not in data:
            print("⚠️ WeatherAPI 無預報資料")
            return None

        cc = OpenCC('s2t')
        forecast_day = data["forecast"]["forecastday"][day_offset]

        if event_hour is not None:
            hour = min(max(event_hour, 0), 23)
            hour_data = forecast_day["hour"][hour]
            desc = cc.convert(hour_data["condition"]["text"])
            temp = hour_data["temp_c"]
            pop = hour_data.get("chance_of_rain", 0)
            uvi = hour_data.get("uv", "N/A")
        else:
            max_uv = 0
            max_temp = -999
            max_pop = 0
            desc = None
            for hour_data in forecast_day["hour"]:
                if hour_data["uv"] > max_uv:
                    max_uv = hour_data["uv"]
                if hour_data["temp_c"] > max_temp:
                    max_temp = hour_data["temp_c"]
                    desc = hour_data["condition"]["text"]
                if hour_data.get("chance_of_rain", 0) > max_pop:
                    max_pop = hour_data.get("chance_of_rain", 0)
            desc = cc.convert(desc) if desc else "天氣資料不足"
            temp = max_temp
            pop = max_pop
            uvi = max_uv

        return f"{desc}，氣溫 {temp}°C，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
    except Exception as e:
        print("❌ WeatherAPI 查詢失敗：", e)
        return "⚠️ 找不到天氣資料"

def send_message(msg):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Authorization': f'Bearer {LINE_TOKEN}', 'Content-Type': 'application/json'}
    payload = {'to': GROUP_ID, 'messages': [{'type': 'text', 'text': msg}]}
    r = requests.post(url, headers=headers, json=payload)
    print("訊息發送結果：", r.status_code, r.text)

@app.route("/")
def index():
    return "Bot is running!"

@app.route("/run", methods=["GET"])
def run():
    events, target_date = get_google_calendar_events()
    offset = (target_date - datetime.date.today()).days
    if not events:
        send_message(f"【{target_date.strftime('%m/%d')} 行程提醒】\n📭 {target_date.strftime('%m/%d')} 沒有安排外出行程，請好好上班:))")
        return "No events."

    lines = [f"【{target_date.strftime('%m/%d')} 行程提醒】"]
    for event in events:
        summary = event.get("summary", "（未命名行程）")
        start_info = event.get("start", {})
        location = event.get("location")
        start_time = start_info.get("dateTime") or start_info.get("date")

        try:
            time_str = datetime.datetime.fromisoformat(start_time).strftime('%H:%M') if "T" in start_time else "(整天)"
            event_hour = datetime.datetime.fromisoformat(start_time).hour if "T" in start_time else None
        except:
            time_str = "(時間錯誤)"
            event_hour = None

        if location:
            coords = geocode_location(location)
            if coords:
                township = get_township_from_coords(*coords)
                query_location = township or location
                weather_info = fetch_weather_by_weatherapi(query_location, offset, event_hour)
            else:
                weather_info = "⚠️ 找不到天氣資料"
            lines.append(f"📌 {time_str}《{summary}》\n📍 地點：{location}\n🌤️ 天氣：{weather_info}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）\n")

    send_message("\n".join(lines))
    return "Checked and sent."

@app.route("/debug", methods=["GET"])
def debug_weather():
    location = request.args.get("location", default="平溪車站")
    coords = geocode_location(location)
    if not coords:
        return f"❌ 找不到地點：{location}"
    township = get_township_from_coords(*coords)
    query_location = township or location
    taiwan_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today_weekday = taiwan_now.weekday()
    offset = 3 if today_weekday == 4 else 1
    weather = fetch_weather_by_weatherapi(query_location, offset, event_hour=8)
    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"🏙️ 查詢地區：{query_location}\n"
        f"🗓️ 今天星期：{today_weekday} (0=Mon, ..., 4=Fri)\n"
        f"➡️ 預計查詢 {offset} 天後的天氣\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
