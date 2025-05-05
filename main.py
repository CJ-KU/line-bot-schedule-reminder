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
CWA_API_KEY = os.getenv("CWA_API_KEY")

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

# Google Maps Text Search + Reverse Geocoding

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
                return loc["lat"], loc["lng"]
        except Exception as e:
            print("❌ Google Places 查詢錯誤：", e)
        return None

def reverse_geocode_town(lat, lng):
    maps_api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not maps_api_key:
        return None
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json"
        params = {"latlng": f"{lat},{lng}", "key": maps_api_key, "language": "zh-TW"}
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if data["status"] == "OK":
            for comp in data["results"][0]["address_components"]:
                if "administrative_area_level_3" in comp["types"]:
                    return comp["long_name"]
        return None
    except Exception as e:
        print("❌ Reverse geocoding 失敗：", e)
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

# 中央氣象署備援天氣預報

def fetch_weather_by_cwa(town_name):
    try:
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089?Authorization={CWA_API_KEY}&locationName={town_name}"
        res = requests.get(url, timeout=5)
        data = res.json()
        if res.status_code == 200 and data["records"]["locations"]:
            location_data = data["records"]["locations"][0]["location"][0]
            elements = {e["elementName"]: e["time"][1]["elementValue"][0]["value"] for e in location_data["weatherElement"]}
            description = elements.get("Wx", "無預報")
            pop = elements.get("PoP12h", "-")
            uvi = elements.get("UVI", "-")
            return f"{description}，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
    except Exception as e:
        print("❌ CWA 天氣查詢失敗：", e)
    return None

# 天氣查詢邏輯

def fetch_weather(lat, lon):
    try:
        # 1. OpenWeatherMap 優先
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
            return f"{d['weather'][0]['description']}，溫度 {round(d['temp']['day'])}°C，降雨機率 {round(d.get('pop', 0)*100)}% ，紫外線 {d.get('uvi', 'N/A')}（{interpret_uv_index(d.get('uvi'))}）"

        # 2. Reverse Geocode → CWA 備援
        town_name = reverse_geocode_town(lat, lon)
        print(f"🔍 Reverse 取得鄉鎮：{town_name}")
        if town_name:
            cwa_result = fetch_weather_by_cwa(town_name)
            if cwa_result:
                return f"📡 使用 CWA 預報：{cwa_result}"

        # 3. 最後固定 fallback：平溪區
        fallback = fetch_weather_by_cwa("平溪區")
        return fallback if fallback else "⚠️ 找不到明天天氣資料"

    except Exception as e:
        print("❌ fetch_weather 失敗：", e)
        return "⚠️ 天氣查詢失敗"

# 傳送 LINE 訊息
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
            weather_info = fetch_weather(*coords) if coords else fetch_weather_by_cwa("平溪區")
            lines.append(f"📌 {time_str}《{summary}》\n📍 地點：{location}\n🌤️ 天氣：{weather_info}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）\n")

    send_message("\n".join(lines))
    return "Checked and sent."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
