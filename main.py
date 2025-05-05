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
CWA_JSON_PATH = os.getenv("CWA_JSON_PATH", "F-D0047-089.json")  # 中央氣象署 JSON 檔案路徑

# 建立 Google Calendar service
def get_calendar_service():
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

# 抓取明日行程
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

# Google Maps 地點查詢
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

# 由座標反查「縣市」名稱
def reverse_geocode_city(lat, lng):
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
                if "administrative_area_level_1" in comp["types"]:
                    print(f"🏙️ 取得縣市：{comp['long_name']}")
                    return comp["long_name"]
        print("⚠️ 找不到行政區（縣市）")
        return None
    except Exception as e:
        print("❌ Reverse geocoding 錯誤：", e)
        return None

# 紫外線等級轉換
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

# 從 JSON 擷取特定城市明天中午天氣
def fetch_weather_by_city(city_name):
    try:
        print(f"📡 從 JSON 查詢天氣：{city_name}")
        with open(CWA_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        locations = data["records"]["locations"][0]["location"]
        for loc in locations:
            if loc["locationName"] == city_name:
                weather_elements = {e["elementName"]: e["time"] for e in loc["weatherElement"]}
                # 取中午 12:00 的時間點
                for i in range(len(weather_elements["Wx"])):
                    t = weather_elements["Wx"][i]["startTime"]
                    if "12:00:00" in t:
                        desc = weather_elements["Wx"][i]["elementValue"][0]["value"]
                        pop = weather_elements["PoP12h"][i]["elementValue"][0]["value"]
                        uvi = weather_elements["UVI"][i]["elementValue"][0]["value"]
                        return f"{desc}，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
        print("⚠️ 找不到指定時間資料")
    except Exception as e:
        print("❌ JSON 天氣解析失敗：", e)
    return "⚠️ 找不到明天天氣資料"

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
            print(f"🧭 查詢地點：{location} → {coords}")
            city = reverse_geocode_city(*coords) if coords else None
            weather_info = fetch_weather_by_city(city) if city else "⚠️ 找不到縣市"
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
    city = reverse_geocode_city(*coords)
    if not city:
        return f"❌ 查無縣市資訊：{coords}"
    weather = fetch_weather_by_city(city)
    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"🏙️ 縣市：{city}\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
