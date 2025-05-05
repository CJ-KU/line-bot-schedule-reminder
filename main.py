from flask import Flask, request
import datetime
import requests
import os
import json
import re
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")
CALENDAR_ID = os.getenv("CALENDAR_ID") or "primary"
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
CWA_XML_PATH = "./F-D0047-089.xml"

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

    coords = search_place(location)
    if coords:
        return coords
    cleaned = clean_location(location)
    if cleaned != location:
        return search_place(cleaned)
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
                if any(word in comp["long_name"] for word in ["區", "鄉", "鎮", "市"]) and "political" in comp["types"]:
                    print(f"🏞️ Fallback 鄉鎮名：{comp['long_name']}")
                    return comp["long_name"]
        print("⚠️ Reverse geocode 找不到合適鄉鎮名")
        return None
    except Exception as e:
        print("❌ Reverse geocoding 失敗：", e)
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

def fetch_weather_from_xml(town_name):
    try:
        tree = ET.parse(CWA_XML_PATH)
        root = tree.getroot()
        tomorrow = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        for location in root.iter("location"):
            name = location.find("locationName").text.strip()
            if name == town_name:
                weather_data = {}
                for element in location.findall("weatherElement"):
                    element_name = element.find("elementName").text
                    for time in element.findall("time"):
                        start_time = time.attrib.get("startTime", "")
                        if tomorrow in start_time and "12:00:00" in start_time:
                            value = time.find("elementValue/value").text
                            weather_data[element_name] = value
                            break
                return f"{weather_data.get('Wx', '無預報')}，降雨機率 {weather_data.get('PoP12h', '-')}% ，紫外線 {weather_data.get('UVI', '-')}（{interpret_uv_index(weather_data.get('UVI', '-'))}）"
        return "⚠️ 找不到明天天氣資料"
    except Exception as e:
        print("❌ XML 解析錯誤：", e)
        return "⚠️ 找不到明天天氣資料"

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
            town = reverse_geocode_town(*coords) if coords else None
            weather_info = fetch_weather_from_xml(town) if town else "⚠️ 找不到地點對應鄉鎮"
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
    weather = fetch_weather_from_xml(town)
    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"🏞️ 鄉鎮：{town}\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
