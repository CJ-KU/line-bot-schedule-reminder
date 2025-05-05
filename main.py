# main.py
from flask import Flask, request
import datetime, os, json, re, requests
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()
app = Flask(__name__)

# 環境變數
LINE_TOKEN = os.getenv("LINE_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")
CALENDAR_ID = os.getenv("CALENDAR_ID") or "primary"
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
CWA_API_KEY = os.getenv("CWA_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# 建立 Google Calendar 服務
def get_calendar_service():
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

# 查詢明日行程
def get_google_calendar_events():
    service = get_calendar_service()
    now = datetime.datetime.utcnow()
    tomorrow = now + datetime.timedelta(days=1)
    start = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day).isoformat() + 'Z'
    end = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59).isoformat() + 'Z'
    events_result = service.events().list(
        calendarId=CALENDAR_ID, timeMin=start, timeMax=end,
        singleEvents=True, orderBy='startTime'
    ).execute()
    return events_result.get('items', [])

# Google Maps 地點轉座標
def geocode_location(location):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": location, "key": GOOGLE_MAPS_API_KEY, "language": "zh-TW"}
    try:
        r = requests.get(url, params=params)
        data = r.json()
        if data["status"] == "OK":
            loc = data["results"][0]["geometry"]["location"]
            print("✅ 轉換地點為座標：", loc)
            return loc["lat"], loc["lng"]
    except Exception as e:
        print("❌ 地點轉座標失敗：", e)
    return None

# 反向座標找鄉鎮名
def reverse_geocode_town(lat, lon):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"latlng": f"{lat},{lon}", "key": GOOGLE_MAPS_API_KEY, "language": "zh-TW"}
    try:
        r = requests.get(url, params=params)
        data = r.json()
        for comp in data["results"][0]["address_components"]:
            if "administrative_area_level_3" in comp["types"]:
                return comp["long_name"]
    except Exception as e:
        print("❌ 反向找鄉鎮名失敗：", e)
    return None

# 紫外線轉中文說明
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

# 查詢 OpenWeatherMap 紫外線
def get_owm_uvi(lat, lon):
    url = "https://api.openweathermap.org/data/2.5/onecall"
    params = {
        "lat": lat, "lon": lon,
        "appid": WEATHER_API_KEY,
        "exclude": "current,minutely,hourly,alerts",
        "units": "metric"
    }
    try:
        r = requests.get(url, params=params)
        d = r.json().get("daily", [{}])[1]
        return d.get("uvi", "N/A")
    except Exception as e:
        print("❌ OWM 紫外線失敗：", e)
        return "N/A"

# 查詢 CWA 鄉鎮預報
def fetch_weather_by_cwa(town_name, lat=None, lon=None):
    try:
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089"
        params = {"Authorization": CWA_API_KEY, "locationName": town_name}
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        location = data["records"]["locations"][0]["location"][0]
        elements = {e["elementName"]: e["time"] for e in location["weatherElement"]}

        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        def get_value(name):
            for item in elements.get(name, []):
                if item["dataTime"].startswith(tomorrow):
                    return item["elementValue"][0].get("value") or item["elementValue"][0].get("Temperature")
            return None

        temp = get_value("溫度")
        feels_like = get_value("體感溫度")
        pop = get_value("降雨機率")
        uvi = get_owm_uvi(lat, lon) if lat and lon else "N/A"
        uvi_note = interpret_uv_index(uvi)
        return f"🌡️ {temp}°C，體感 {feels_like}°C，🌧️ 降雨 {pop}%，☀️ 紫外線 {uvi}（{uvi_note}）"
    except Exception as e:
        print("❌ CWA 天氣資料取得失敗：", e)
        return "⚠️ 找不到明天天氣資料"

# 發送 LINE 訊息
def send_message(msg):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {'Authorization': f'Bearer {LINE_TOKEN}', 'Content-Type': 'application/json'}
    payload = {'to': GROUP_ID, 'messages': [{'type': 'text', 'text': msg}]}
    r = requests.post(url, headers=headers, json=payload)
    print("LINE 傳送結果：", r.status_code)

@app.route("/")
def index():
    return "Bot is running!"

@app.route("/run", methods=["GET"])
def run():
    events = get_google_calendar_events()
    if not events:
        return "No events."
    lines = ["【明日行程提醒】"]
    for event in events:
        summary = event.get("summary", "（未命名）")
        location = event.get("location")
        start = event["start"].get("dateTime") or event["start"].get("date")
        time_str = datetime.datetime.fromisoformat(start).strftime('%H:%M') if "T" in start else "(整天)"

        if location:
            coords = geocode_location(location)
            town = reverse_geocode_town(*coords) if coords else None
            weather = fetch_weather_by_cwa(town, *coords) if town else "⚠️ 找不到地點資料"
            lines.append(f"📌 {time_str}《{summary}》\n📍 {location}\n🌤️ {weather}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）")
    send_message("\n".join(lines))
    return "行程已提醒"

@app.route("/debug", methods=["GET"])
def debug_weather():
    location = request.args.get("location", default="信義區")
    coords = geocode_location(location)
    town = reverse_geocode_town(*coords) if coords else None
    weather = fetch_weather_by_cwa(town, *coords) if town else "⚠️ 查無天氣資料"
    return f"✅ 測試地點：{location}\n📍 座標：{coords}\n🌤️ 天氣：{weather}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
