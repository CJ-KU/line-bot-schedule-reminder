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
Maps_API_KEY = os.getenv("Maps_API_KEY") # 確保你的環境變數名稱是 Maps_API_KEY
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
    if weekday == 4:  # Friday (星期五)
        return today + datetime.timedelta(days=3) # 目標是下週一
    else:
        return today + datetime.timedelta(days=1) # 目標是明天

def get_google_calendar_events():
    service = get_calendar_service()
    target_date = get_target_date()
    # 這裡的 timeMin 和 timeMax 需要調整為 UTC 時間
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
        # **重要修改：改用 Google Places API 的 Text Search**
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": location, # 使用 'query' 參數
            "key": Maps_API_KEY,
            "region": "tw", # 限制在台灣地區
            "language": "zh-TW" # 返回繁體中文結果
        }
        res = requests.get(url, params=params, timeout=5)
        response_json = res.json() # 先解析一次
        results = response_json.get("results", [])

        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
        else:
            print(f"⚠️ 無法從 Places Text Search API 找到：{location} → 回傳：{json.dumps(response_json, ensure_ascii=False)}")
    except Exception as e:
        print(f"❌ 地點查詢失敗：{e}")
    return None

def get_township_from_coords(lat, lon):
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "latlng": f"{lat},{lon}",
            "key": Maps_API_KEY,
            "language": "zh-TW"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if data["status"] == "OK":
            level2 = None # 通常是區/鄉/鎮 (行政區劃等級2)
            level1 = None # 通常是縣/市 (行政區劃等級1)
            # 遍歷地址組件，尋找行政區
            for comp in data["results"][0]["address_components"]:
                if "administrative_area_level_2" in comp["types"]:
                    level2 = comp["long_name"]
                if "administrative_area_level_1" in comp["types"]:
                    level1 = comp["long_name"]
            
            # 優先組合縣市和區，如果沒有區則只返回縣市
            if level2 and level1:
                return f"{level1}{level2}"
            elif level1:
                return level1
        else:
            print(f"⚠️ Reverse Geocoding API 狀態：{data['status']} → 回傳：{json.dumps(data, ensure_ascii=False)}")
    except Exception as e:
        print(f"❌ 解析行政區失敗：{e}")
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

def fetch_weather_by_weatherapi(location_name, day_offset):
    try:
        url = "https://api.weatherapi.com/v1/forecast.json"
        params = {
            "key": WEATHERAPI_KEY,
            "q": location_name,
            "days": day_offset + 1,  # WeatherAPI免費版提供3小時預報，最多5天
            "lang": "zh"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        
        # 檢查是否有預報資料
        if "forecast" not in data or not data["forecast"]["forecastday"]:
            print(f"⚠️ WeatherAPI 無預報資料或 forecastday 為空: {json.dumps(data, ensure_ascii=False)}")
            return "⚠️ 找不到天氣資料"
        
        # 取得目標日期的預報
        # 這裡 day_offset 是從今天算起的天數差，例如 day_offset=0是今天, day_offset=1是明天
        # target_day_forecast 是目標日期那一天的所有 3 小時預報數據
        if day_offset >= len(data["forecast"]["forecastday"]):
            print(f"⚠️ WeatherAPI 無 {day_offset} 天後的預報資料。")
            return "⚠️ 找不到該日天氣資料"

        target_day_forecast = data["forecast"]["forecastday"][day_offset]
        
        # 尋找最接近中午時段 (12:00-14:00) 的預報
        closest_noon_forecast = None
        min_diff = float('inf') # 用來記錄時間差的最小值
        
        # 確保 'hour' 鍵存在且是一個列表且不為空
        if 'hour' in target_day_forecast and isinstance(target_day_forecast['hour'], list) and target_day_forecast['hour']:
            for hour_data in target_day_forecast['hour']:
                try:
                    # 解析預報時間
                    forecast_time = datetime.datetime.strptime(hour_data["time"], "%Y-%m-%d %H:%M")
                    
                    # 計算預報時間與 12:00 的小時差
                    # 我們希望優先找到 12 點的預報，如果沒有，就找最接近的
                    hour_diff = abs(forecast_time.hour - 12)
                    
                    if hour_diff < min_diff:
                        min_diff = hour_diff
                        closest_noon_forecast = hour_data
                    elif hour_diff == min_diff:
                        # 如果有相同的小時差 (例如 11:00 和 13:00 都差 1 小時)，選擇時間更接近中午的
                        # 這裡我們選擇時間較晚的，通常會更靠近實際的「中午」感受
                        if closest_noon_forecast is None or forecast_time.hour > datetime.datetime.strptime(closest_noon_forecast["time"], "%Y-%m-%d %H:%M").hour:
                            closest_noon_forecast = hour_data

                except ValueError:
                    # 時間格式不正確，跳過此筆數據
                    continue

        if closest_noon_forecast:
            cc = OpenCC('s2t')
            desc = cc.convert(closest_noon_forecast["condition"]["text"])
            temp = closest_noon_forecast["temp_c"]
            # 3小時預報提供的是該時段的降雨機率 `chance_of_rain`
            pop = closest_noon_forecast.get("chance_of_rain", "N/A")
            # 3小時預報中通常沒有 UVI，但有些情況下可能會包含
            uvi = closest_noon_forecast.get("uv", "N/A") 
            
            # 因為是單一時間點的溫度，所以沒有溫度區間
            temp_display = f"{temp}°C"
            
            return f"{desc}，約 {temp_display}，降雨機率 {pop}%，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
        else:
            return "⚠️ 無法取得該日中午時段天氣資料"

    except Exception as e:
        print(f"❌ WeatherAPI 查詢失敗：{e}")
        # 如果是 JSON 解碼錯誤，印出原始文本方便偵錯
        if 'res' in locals():
            print(f"原始回應文本: {res.text}")
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
    
    # 確保 offset 不會超出 WeatherAPI 免費方案的 5 天限制
    if offset >= 5:
        send_message(f"【{target_date.strftime('%m/%d')} 行程提醒】\n📭 {target_date.strftime('%m/%d')} 的日期超出天氣預報範圍（僅支援未來5天）。")
        return "Date out of weather forecast range."

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
                township = get_township_from_coords(*coords)
                query_location = township or location # 優先使用行政區名稱查詢天氣
                weather_info = fetch_weather_by_weatherapi(query_location, offset)
            else:
                weather_info = "⚠️ 無法取得地點座標，跳過天氣查詢。"
            lines.append(f"📌 {time_str}《{summary}》\n📍 地點：{location}\n🌤️ 天氣（中午時段）：{weather_info}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）\n")
    
    send_message("\n".join(lines))
    return "Checked and sent."

@app.route("/debug", methods=["GET"])
def debug_weather():
    location = request.args.get("location", default="台北市信義區") # 預設地點
    coords = geocode_location(location)
    if not coords:
        return f"❌ 找不到地點：{location}"
    
    township = get_township_from_coords(*coords)
    query_location = township or location # 優先使用行政區名稱查詢天氣
    
    taiwan_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today_weekday = taiwan_now.weekday()
    
    # 計算 target_date 並獲取 offset
    target_date = get_target_date()
    offset = (target_date - datetime.date.today()).days

    # 確保 offset 不會超出 WeatherAPI 免費方案的 5 天限制
    if offset >= 5:
        return f"【DEBUG】\n⚠️ 預計查詢的日期超出 WeatherAPI 免費方案的 5 天預報範圍。"

    weather = fetch_weather_by_weatherapi(query_location, offset)
    
    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"🏙️ 查詢地區：{query_location}\n"
        f"🗓️ 今天星期：{today_weekday} (0=Mon, ..., 4=Fri)\n"
        f"➡️ 預計查詢 {offset} 天後的天氣 (此天氣為該日中午時段預報)\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
