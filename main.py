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

def fetch_weather_by_coords(lat, lon, day_offset):
    try:
        url = "https://api.weatherapi.com/v1/forecast.json"
        params = {
            "key": WEATHERAPI_KEY,
            "q": f"{lat},{lon}",
            "days": day_offset + 1,
            "lang": "zh"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()

        if "forecast" not in data or not data["forecast"].get("forecastday"):
            print("⚠️ WeatherAPI 無預報資料")
            return "⚠️ 找不到天氣資料"

        target_day_forecast = data["forecast"]["forecastday"][day_offset]
        closest_noon_forecast = min(
            target_day_forecast["hour"],
            key=lambda h: abs(datetime.datetime.strptime(h["time"], "%Y-%m-%d %H:%M").hour - 12)
        )
        cc = OpenCC('s2t')
        desc = cc.convert(closest_noon_forecast["condition"]["text"])
        temp = closest_noon_forecast["temp_c"]
        pop = closest_noon_forecast.get("chance_of_rain", "N/A")
        uvi = closest_noon_forecast.get("uv", "N/A")
        temp_display = f"{temp}°C"
        return f"{desc}，約 {temp_display}，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
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
        except:
            time_str = "(時間錯誤)"

        if location:
            coords = geocode_location(location)
            if coords:
                weather_info = fetch_weather_by_coords(*coords, offset)
            else:
                weather_info = "⚠️ 無法取得地點座標，跳過天氣查詢。"
            lines.append(f"📌 {time_str}《{summary}》\n📍 地點：{location}\n🌤️ 天氣：{weather_info}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）\n")

    send_message("\n".join(lines))
    return "Checked and sent."

@app.route("/debug", methods=["GET"])
def debug_weather():
    location = request.args.get("location", default="台北市信義區")
    coords = geocode_location(location)
    if not coords:
        return f"❌ 找不到地點：{location}"
    offset = 3 if datetime.datetime.utcnow().weekday() == 4 else 1
    weather = fetch_weather_by_coords(*coords, offset)
    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"➡️ 預計查詢 {offset} 天後的天氣（中午時段）\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
