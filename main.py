from flask import Flask, request
import datetime
import requests
import os
import json
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
    service = build('calendar', 'v3', credentials=creds)
    return service

# 列出目前帳戶下所有日曆清單
@app.route("/calendars", methods=["GET"])
def list_calendars():
    service = get_calendar_service()
    calendar_list = service.calendarList().list().execute()
    results = []
    for item in calendar_list.get("items", []):
        results.append({
            "summary": item.get("summary"),
            "id": item.get("id")
        })
    return json.dumps(results, indent=2, ensure_ascii=False)

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

# 用 Google Maps API 取得座標
def geocode_location(location):
    maps_api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not maps_api_key:
        return None

    # Step 1: 使用 Google Places Text Search API 搜尋地點
    search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    search_params = {
        "query": location,
        "key": maps_api_key,
        "region": "tw",  # 限定台灣區域（可依需求調整）
        "language": "zh-TW"
    }

    try:
        search_response = requests.get(search_url, params=search_params, timeout=5)
        search_data = search_response.json()

        if search_data["status"] == "OK" and len(search_data["results"]) > 0:
            loc = search_data["results"][0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
        else:
            print("❌ Place Text Search 失敗：", search_data.get("status"), search_data.get("error_message", ""))
    except Exception as e:
        print("❌ Google Places 查詢失敗：", e)

    return None

# 用經緯度查天氣
def interpret_uv_index(uvi):
    try:
        uvi = float(uvi)
        if uvi <= 2:
            return f"🟢 低"
        elif uvi <= 5:
            return f"🟡 中等"
        elif uvi <= 7:
            return f"🟠 高"
        elif uvi <= 10:
            return f"🔴 很高"
        else:
            return f"🟣 極高"
    except:
        return "❓ 未知"

def fetch_weather_by_coords(lat, lon):
    api_key = os.getenv("WEATHER_API_KEY")
    if not api_key:
        return "⚠️ 無法取得 API 金鑰"

    url = "https://api.openweathermap.org/data/2.5/onecall"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": "metric",
        "lang": "zh_tw",
        "exclude": "minutely,hourly,alerts"
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        data = response.json()

        if response.status_code != 200 or "daily" not in data:
            return "⚠️ 找不到明天天氣資料"

        tomorrow = data["daily"][1]
        description = tomorrow["weather"][0]["description"]
        temp = round(tomorrow["temp"]["day"])  # 白天平均溫度
        pop = round(tomorrow.get("pop", 0) * 100)  # 降雨機率 (%)
        uvi = tomorrow.get("uvi", "N/A")
        uv_level = interpret_uv_index(uvi)

        return f"{description}，溫度 {temp}°C，降雨機率 {pop}% ，紫外線 {uvi}（{uv_level}）"
    except Exception as e:
        print("❌ 天氣查詢失敗：", e)
        return "⚠️ 天氣查詢失敗"


# 傳送 LINE 訊息
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

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        body = request.get_data(as_text=True)
        print("✅ Webhook raw body:")
        print(body)
        json_body = json.loads(body)
        print("✅ Webhook parsed JSON:")
        print(json.dumps(json_body, indent=2))
    except Exception as e:
        print("❌ Error parsing webhook:", e)
    return "OK", 200

@app.route("/")
def index():
    return "Bot is running!"

@app.route("/run", methods=["GET"])
def run():
    events = get_google_calendar_events()
    if not events:
        return "No events for tomorrow."

    message_lines = ["【明日行程提醒】"]
    for event in events:
        summary = event.get("summary", "（未命名行程）")
        start_info = event.get("start", {})
        location = event.get("location")

        start_time = start_info.get("dateTime") or start_info.get("date")
        if "T" in start_time:
            time_str = datetime.datetime.fromisoformat(start_time).strftime('%H:%M')
        else:
            time_str = "(整天)"

        if location:
            coords = geocode_location(location)
            if coords:
                weather_info = fetch_weather_by_coords(*coords)
            else:
                weather_info = "⚠️ 地點轉換失敗"
            message_lines.append(
                f"📌 {time_str}《{summary}》\n"
                f"📍 地點：{location}\n"
                f"🌤️ 天氣：{weather_info}\n"
            )
        else:
            message_lines.append(f"📌 {time_str}《{summary}》（無地點）\n")

    send_message("\n".join(message_lines))
    return "Checked and sent if needed."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
