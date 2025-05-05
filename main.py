from flask import Flask, request
import datetime
import requests
import os
import json
import re
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")
CALENDAR_ID = os.getenv("CALENDAR_ID") or "primary"
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
CWA_API_KEY = os.getenv("CWA_API_KEY")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

def get_calendar_service():
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

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

def geocode_location(location):
    try:
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": location,
            "key": GOOGLE_MAPS_API_KEY,
            "region": "tw",
            "language": "zh-TW"
        }
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        results = response.json()["results"]
        if results:
            loc = results[0]["geometry"]["location"]
            print(f"✅ 地點查詢成功：{location} → {(loc['lat'], loc['lng'])}")
            return loc["lat"], loc["lng"]
    except Exception as e:
        print(f"❌ 地點查詢錯誤：{e}")
    return None

def reverse_geocode_town(lat, lng):
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "latlng": f"{lat},{lng}",
            "key": GOOGLE_MAPS_API_KEY,
            "language": "zh-TW"
        }
        res = requests.get(url, params=params)
        res.raise_for_status()
        data = res.json()
        if data["status"] == "OK" and data["results"]:
            for comp in data["results"][0]["address_components"]:
                if "administrative_area_level_3" in comp["types"]:
                    print(f"🏞️ 取得鄉鎮市區：{comp['long_name']}")
                    return comp["long_name"]
        print("⚠️ 找不到行政區（鄉鎮市區）")
    except Exception as e:
        print(f"❌ Reverse geocoding 錯誤：{e}")
    return None

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

def fetch_weather_from_cwa(town_name, target_datetime):
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089"
        params = {
            "Authorization": CWA_API_KEY,
            "locationName": town_name
        }
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()
        if data["records"]["locations"]:
            location_data = data["records"]["locations"][0]["location"][0]
            weather_elements = location_data["weatherElement"]
            weather_info = {}
            for element in weather_elements:
                for time_block in element["time"]:
                    start = datetime.datetime.fromisoformat(time_block["startTime"].replace("+08:00", ""))
                    end = datetime.datetime.fromisoformat(time_block["endTime"].replace("+08:00", ""))
                    if start <= target_datetime <= end:
                        if element["elementName"] == "WeatherDescription":
                            weather_info["Wx"] = time_block["elementValue"][0]["value"]
                        elif element["elementName"] == "PoP12h":
                            weather_info["PoP12h"] = time_block["elementValue"][0]["value"]
                        elif element["elementName"] == "UVI":
                            weather_info["UVI"] = time_block["elementValue"][0]["value"]
            wx = weather_info.get("Wx", "無資料")
            pop = weather_info.get("PoP12h", "-")
            uvi = weather_info.get("UVI", "-")
            return f"{wx}，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
    except Exception as e:
        print(f"❌ CWA API 錯誤：{e}")
    return "⚠️ 找不到明天天氣資料"

def send_message(msg):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Authorization': f'Bearer {LINE_TOKEN}',
        'Content-Type': 'application/json'
    }
    payload = {
        'to': GROUP_ID,
        'messages': [{'type': 'text', 'text': msg}]
    }
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
        tomorrow_noon = datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)

        if location:
            coords = geocode_location(location)
            print(f"🧭 查詢地點：{location} → {coords}")
            town = reverse_geocode_town(*coords) if coords else None
            weather_info = fetch_weather_from_cwa(town, tomorrow_noon) if town else "⚠️ 找不到鄉鎮市區資訊"
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
    town = reverse_geocode_town(*coords)
    if not town:
        return f"❌ 查無鄉鎮資訊：{coords}"
    target_time = datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    weather = fetch_weather_from_cwa(town, target_time)
    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"🏞️ 鄉鎮：{town}\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
