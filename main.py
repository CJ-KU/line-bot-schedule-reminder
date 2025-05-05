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
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
CWA_API_KEY = os.getenv("CWA_API_KEY")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# 建立 Google Calendar 服務
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

# 地點轉經緯度
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

# 經緯度轉行政區
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
        print("❌ 取得行政區失敗：", e)
    return None

# 紫外線等級轉中文
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

# 查 CWA（中央氣象署）天氣（取明日任一筆）
def fetch_weather_by_cwa(location_name):
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089"
        params = {
            "Authorization": CWA_API_KEY,
            "locationName": location_name
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()

        if not data.get("success") or "records" not in data:
            print("⚠️ CWA 回傳失敗")
            return None

        locations = data["records"]["locations"][0]["location"]
        if not locations:
            print("⚠️ 找不到地點資料：", location_name)
            return None

        weather_data = {}
        elements = locations[0]["weatherElement"]
        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

        # Debug：列出每項天氣要素的時間
        print(f"🔍 {location_name} 各元素可用時間：")
        for elem in elements:
            print(f"  ⮕ {elem['elementName']}: {[t['startTime'] for t in elem['time'][:3]]}")

        for elem in elements:
            name = elem["elementName"]
            for time_entry in elem["time"]:
                if time_entry["startTime"].startswith(tomorrow):
                    weather_data[name] = time_entry["elementValue"][0]["value"]
                    break

        desc = weather_data.get("WeatherDescription", "無資料")
        temp = weather_data.get("MaxT", "N/A")
        pop = weather_data.get("PoP12h", "N/A")
        uvi = weather_data.get("UVIndex", "N/A")

        return f"{desc}，溫度 {temp}°C，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
    except Exception as e:
        print("❌ CWA 天氣查詢錯誤：", e)
        return None

# 查 OpenWeather 天氣（備援）
def fetch_weather_by_openweather(lat, lon):
    try:
        url = "https://api.openweathermap.org/data/2.5/onecall"
        params = {
            "lat": lat,
            "lon": lon,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
            "lang": "zh_tw",
            "exclude": "current,minutely,hourly,alerts"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        daily = data.get("daily", [])
        if len(daily) >= 2:
            d = daily[1]
        elif len(daily) == 1:
            d = daily[0]
        else:
            return "⚠️ 天氣預報資料不足"

        desc = d['weather'][0]['description']
        temp = round(d['temp']['day'])
        pop = round(d.get('pop', 0) * 100)
        uvi = d.get('uvi', 'N/A')
        return f"{desc}，溫度 {temp}°C，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
    except Exception as e:
        print("❌ OpenWeatherMap 天氣查詢錯誤：", e)
    return "⚠️ 找不到明天天氣資料"

# 傳送 LINE Bot 訊息
def send_message(msg):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Authorization': f'Bearer {LINE_TOKEN}', 'Content-Type': 'application/json'}
    payload = {'to': GROUP_ID, 'messages': [{'type': 'text', 'text': msg}]}
    r = requests.post(url, headers=headers, json=payload)
    print("訊息發送結果：", r.status_code, r.text)

@app.route("/")
def index():
    return "Bot is running!"

# 自動推播明日行程
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
                weather_info = fetch_weather_by_cwa(township) if township else None
                if not weather_info:
                    weather_info = fetch_weather_by_openweather(*coords)
            else:
                weather_info = "⚠️ 找不到明天天氣資料"

            lines.append(f"📌 {time_str}《{summary}》\n📍 地點：{location}\n🌤️ 天氣：{weather_info}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）\n")

    send_message("\n".join(lines))
    return "Checked and sent."

# 手動測試天氣查詢
@app.route("/debug", methods=["GET"])
def debug_weather():
    location = request.args.get("location", default="平溪車站")
    coords = geocode_location(location)
    if not coords:
        return f"❌ 找不到地點：{location}"
    township = get_township_from_coords(*coords)
    weather = fetch_weather_by_cwa(township) or fetch_weather_by_openweather(*coords)
    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"🏙️ 鄉鎮：{township or '未知'}\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
