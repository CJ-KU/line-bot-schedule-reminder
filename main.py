from flask import Flask, request
import datetime
import requests
import os
import json
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
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Google Calendar 驗證
def get_calendar_service():
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

# 取得明日行程
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

# 地點文字 → 經緯度
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

# 經緯度 → 鄉鎮市區（行政區名稱）
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

# 紫外線解釋
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

# WeatherAPI 查天氣
def fetch_weather_by_weatherapi(location_name):
    try:
        url = "https://api.weatherapi.com/v1/forecast.json"
        params = {
            "key": WEATHERAPI_KEY,
            "q": location_name,
            "days": 2,
            "lang": "zh"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()

        if "forecast" not in data:
            print("⚠️ WeatherAPI 無預報資料")
            return None

        tomorrow = data["forecast"]["forecastday"][1]["day"]
        desc = tomorrow["condition"]["text"]
        maxtemp = tomorrow["maxtemp_c"]
        mintemp = tomorrow["mintemp_c"]
        pop = tomorrow.get("daily_chance_of_rain", "N/A")
        uvi = tomorrow.get("uv", "N/A")

        # 若溫差過大，使用簡化顯示
        if abs(maxtemp - mintemp) > 10:
            temp_display = f"{maxtemp}°C（單站估值）"
        else:
            temp_display = f"{mintemp}～{maxtemp}°C"

        return f"{desc}，氣溫 {temp_display}，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"

    except Exception as e:
        print("❌ WeatherAPI 查詢失敗：", e)
        return "⚠️ 找不到明天天氣資料"

# 傳 LINE 訊息
def send_message(msg):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Authorization': f'Bearer {LINE_TOKEN}', 'Content-Type': 'application/json'}
    payload = {'to': GROUP_ID, 'messages': [{'type': 'text', 'text': msg}]}
    r = requests.post(url, headers=headers, json=payload)
    print("訊息發送結果：", r.status_code, r.text)

@app.route("/")
def index():
    return "Bot is running!"

# 自動推播
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
        try:
            time_str = datetime.datetime.fromisoformat(start_time).strftime('%H:%M') if "T" in start_time else "(整天)"
        except:
            time_str = "(時間錯誤)"

        if location:
            coords = geocode_location(location)
            if coords:
                township = get_township_from_coords(*coords)
                query_location = township or location
                weather_info = fetch_weather_by_weatherapi(query_location)
            else:
                weather_info = "⚠️ 找不到明天天氣資料"

            lines.append(f"📌 {time_str}《{summary}》\n📍 地點：{location}\n🌤️ 天氣：{weather_info}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）\n")

    send_message("\n".join(lines))
    return "Checked and sent."

# 手動測試
@app.route("/debug", methods=["GET"])
def debug_weather():
    location = request.args.get("location", default="平溪車站")
    coords = geocode_location(location)
    if not coords:
        return f"❌ 找不到地點：{location}"

    township = get_township_from_coords(*coords)
    query_location = township or location
    weather = fetch_weather_by_weatherapi(query_location)

    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"🏙️ 查詢地區：{query_location}\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
