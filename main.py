from flask import Flask, request
import datetime
import requests
import os
import json
import re
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from lunarcalendar import Converter, Solar

load_dotenv()

app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")
CALENDAR_ID = os.getenv("CALENDAR_ID") or "primary"
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# 建立 Google Calendar service
def get_calendar_service():
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

# 查詢明日行程
def get_google_calendar_events():
    service = get_calendar_service()
    now = datetime.datetime.utcnow()
    tomorrow = now + datetime.timedelta(days=1)
    start = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0).isoformat() + 'Z'
    end = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59).isoformat() + 'Z'
    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=start, timeMax=end,
        singleEvents=True, orderBy='startTime'
    ).execute()
    return events_result.get('items', [])

# Google Maps Text Search

def geocode_location(location):
    maps_api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not maps_api_key:
        return None

    def clean_location(loc):
        return re.sub(r"\(.*?\)|（.*?）", "", loc).strip()

    def search_place(query):
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {"query": query, "key": maps_api_key, "region": "tw", "language": "zh-TW"}
        try:
            response = requests.get(url, params=params, timeout=5)
            data = response.json()
            if data["status"] == "OK" and data["results"]:
                loc = data["results"][0]["geometry"]["location"]
                print(f"✅ 地點查詢成功：{query} → {loc}")
                return loc["lat"], loc["lng"]
            else:
                print(f"❌ 查無地點：{query} → {data.get('status')}")
        except Exception as e:
            print("❌ Google Places 查詢錯誤：", e)
        return None

    coords = search_place(location)
    if coords:
        return coords
    cleaned = clean_location(location)
    if cleaned != location:
        return search_place(cleaned)
    return None

# 紫外線等級解釋
def interpret_uv_index(uvi):
    try:
        uvi = float(uvi)
        if uvi <= 2:
            return "🟢 低"
        elif uvi <= 5:
            return "🟡 中等"
        elif uvi <= 7:
            return "🟠 高"
        elif uvi <= 10:
            return "🔴 很高"
        else:
            return "🟣 極高"
    except:
        return "❓ 未知"

# 嘗試附近地點

def try_nearby_forecast(lat, lon):
    offsets = [(-0.05, 0), (0.05, 0), (0, -0.05), (0, 0.05)]
    for dlat, dlon in offsets:
        alt_lat, alt_lon = lat + dlat, lon + dlon
        print(f"🔄 嘗試附近座標：{alt_lat}, {alt_lon}")
        forecast = fetch_weather_by_coords_single(alt_lat, alt_lon)
        if forecast:
            return f"📍 附近預報：{forecast}"
    return None

def fetch_weather_by_coords_single(lat, lon):
    try:
        url = "https://api.openweathermap.org/data/2.5/onecall"
        params = {
            "lat": lat, "lon": lon,
            "appid": os.getenv("WEATHER_API_KEY"),
            "units": "metric", "lang": "zh_tw",
            "exclude": "minutely,hourly,alerts"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if res.status_code == 200 and "daily" in data and len(data["daily"]) >= 2:
            d = data["daily"][1]
            return f"{d['weather'][0]['description']}，溫度 {round(d['temp']['day'])}°C，" + \
                   f"降雨機率 {round(d.get('pop', 0)*100)}% ，紫外線 {d.get('uvi', 'N/A')}（{interpret_uv_index(d.get('uvi'))}）"
        return None
    except Exception as e:
        print("❌ Nearby 天氣查詢失敗：", e)
        return None

# 天氣查詢

def fetch_weather_by_coords(lat, lon):
    api_key = os.getenv("WEATHER_API_KEY")
    if not api_key:
        return "⚠️ 無法取得 API 金鑰"

    url = "https://api.openweathermap.org/data/2.5/onecall"
    params = {
        "lat": lat, "lon": lon,
        "appid": api_key, "units": "metric",
        "lang": "zh_tw", "exclude": "minutely,hourly,alerts"
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        print(f"🌐 查詢天氣座標：{lat}, {lon}")
        print("🧪 OpenWeather 回傳 daily：", data.get("daily"))

        if response.status_code == 200 and "daily" in data and len(data["daily"]) >= 2:
            d = data["daily"][1]
            return f"{d['weather'][0]['description']}，溫度 {round(d['temp']['day'])}°C，" + \
                   f"降雨機率 {round(d.get('pop', 0)*100)}% ，紫外線 {d.get('uvi', 'N/A')}（{interpret_uv_index(d.get('uvi'))}）"

        print("⚠️ 無 daily 預報，嘗試附近地點")
        nearby = try_nearby_forecast(lat, lon)
        if nearby:
            return nearby

        if "current" in data:
            c = data["current"]
            return f"⚠️ 使用即時天氣：{c['weather'][0]['description']}，溫度 {round(c['temp'])}°C，" + \
                   f"紫外線 {c.get('uvi', 'N/A')}（{interpret_uv_index(c.get('uvi'))}）"

        print("⚠️ OpenWeather 無資料：", data)
        return "⚠️ 找不到明天天氣資料"

    except Exception as e:
        print("❌ 天氣查詢失敗：", e)
        return "⚠️ 天氣查詢失敗"

# 傳送 LINE 訊息

def send_message(msg):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Authorization': f'Bearer {LINE_TOKEN}', 'Content-Type': 'application/json'}
    payload = {'to': GROUP_ID, 'messages': [{'type': 'text', 'text': msg}]}
    r = requests.post(url, headers=headers, json=payload)
    print("訊息發送結果：", r.status_code, r.text)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        body = request.get_data(as_text=True)
        print("✅ Webhook raw body:\n", body)
        json_body = json.loads(body)
        print("✅ Webhook parsed JSON:\n", json.dumps(json_body, indent=2))
    except Exception as e:
        print("❌ Webhook 處理失敗：", e)
    return "OK", 200

@app.route("/")
def index():
    return "Bot is running!"

@app.route("/run", methods=["GET"])
def run():
    events = get_google_calendar_events()
    if not events:
        return "No events for tomorrow."

    lines = ["【明日行程提醒】"]
    for event in events:
        summary = event.get("summary", "（未命名行程）")
        start_info = event.get("start", {})
        location = event.get("location")
        start_time = start_info.get("dateTime") or start_info.get("date")
        time_str = datetime.datetime.fromisoformat(start_time).strftime('%H:%M') if "T" in start_time else "(整天)"

        if location:
            coords = geocode_location(location)
            weather_info = fetch_weather_by_coords(*coords) if coords else "⚠️ 地點轉換失敗"
            lines.append(f"📌 {time_str}《{summary}》\n📍 地點：{location}\n🌤️ 天氣：{weather_info}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）\n")

    send_message("\n".join(lines))
    return "Checked and sent."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
