from flask import Flask, request
import datetime
import requests
import os
import json
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from opencc import OpenCC

# --- 環境變數載入 ---
load_dotenv()
app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")
CALENDAR_ID = os.getenv("CALENDAR_ID") or "primary"
Maps_API_KEY = os.getenv("Maps_API_KEY")
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# --- Google Calendar 服務 ---
def get_calendar_service():
    """獲取 Google Calendar 服務物件。"""
    credentials_info = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

# --- 目標日期計算 ---
def get_target_date():
    """根據當前日期計算目標提醒日期。"""
    taiwan_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today = taiwan_now.date()
    weekday = today.weekday() # 0 = 星期一, 4 = 星期五
    # 如果今天是星期五 (4)，則目標日期是 3 天後 (下週一)
    # 否則，目標日期是 1 天後 (明天)
    return today + datetime.timedelta(days=3) if weekday == 4 else today + datetime.timedelta(days=1)

# --- 獲取 Google Calendar 活動 ---
def get_google_calendar_events():
    """從 Google Calendar 獲取目標日期的活動。"""
    service = get_calendar_service()
    target_date = get_target_date()
    # 將目標日期轉換為 UTC 時間範圍，因為 Google Calendar API 使用 UTC
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

# --- Google Maps 地理編碼 ---
def geocode_location(location):
    """將地點名稱轉換為經緯度座標。"""
    try:
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": location,
            "key": Maps_API_KEY,
            "region": "tw", # 限制在台灣地區
            "language": "zh-TW" # 返回繁體中文結果
        }
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status() # 如果狀態碼不是 2xx，拋出 HTTPError
        results = res.json().get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except requests.exceptions.Timeout:
        print(f"❌ 地點查詢超時：{location}")
    except requests.exceptions.RequestException as e:
        print(f"❌ 地點查詢失敗（網路或其他請求問題）：{e}")
    except Exception as e:
        print(f"❌ 地點查詢失敗：{e}")
    return None

# --- 從座標獲取行政區 ---
def get_township_from_coords(lat, lon):
    """從經緯度座標獲取鄉鎮/行政區名稱。"""
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "latlng": f"{lat},{lon}",
            "key": Maps_API_KEY,
            "language": "zh-TW"
        }
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()
        if data["status"] == "OK":
            # 遍歷地址組成部分，尋找行政區等級 3 (通常是鄉鎮)
            for comp in data["results"][0]["address_components"]:
                if "administrative_area_level_3" in comp["types"]:
                    return comp["long_name"]
        elif data["status"] == "ZERO_RESULTS":
            print(f"⚠️ 找不到座標 ({lat},{lon}) 的行政區結果。")
        else:
            print(f"⚠️ 解析行政區失敗：Google Maps API 狀態 {data['status']}")
    except requests.exceptions.Timeout:
        print(f"❌ 解析行政區超時：({lat},{lon})")
    except requests.exceptions.RequestException as e:
        print(f"❌ 解析行政區失敗（網路或其他請求問題）：{e}")
    except Exception as e:
        print(f"❌ 解析行政區失敗：{e}")
    return None

# --- 紫外線指數解釋 ---
def interpret_uv_index(uvi):
    """根據紫外線指數判斷等級。"""
    try:
        uvi = float(uvi)
        if uvi <= 2: return "🟢 低"
        elif uvi <= 5: return "🟡 中等"
        elif uvi <= 7: return "🟠 高"
        elif uvi <= 10: return "🔴 很高"
        else: return "🟣 極高"
    except:
        return "❓ 未知"

# --- 透過 WeatherAPI 獲取天氣資訊 ---
def fetch_weather_by_weatherapi(location_name, day_offset, event_hour=None):
    """從 WeatherAPI 獲取指定地點和日期的天氣預報。"""
    try:
        url = "https://api.weatherapi.com/v1/forecast.json"
        params = {
            "key": WEATHERAPI_KEY,
            "q": location_name,
            "days": day_offset + 1, # WeatherAPI 的 days 參數包含當天，所以要加 1
            "lang": "zh" # 獲取中文天氣描述
        }
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()

        if "forecast" not in data or not data["forecast"]["forecastday"]:
            print(f"⚠️ WeatherAPI 無 {location_name} 的預報資料。")
            return "⚠️ 找不到天氣資料"

        cc = OpenCC('s2t') # 用於簡體到繁體的轉換
        forecast_day = data["forecast"]["forecastday"][day_offset] # 獲取目標日期的預報

        if event_hour is not None:
            # 如果有指定活動小時，則取該小時的詳細預報
            hour = min(max(event_hour, 0), 23) # 確保小時在 0-23 範圍內
            hour_data = forecast_day["hour"][hour]
            desc = cc.convert(hour_data["condition"]["text"])
            temp = hour_data["temp_c"]
            pop = hour_data.get("chance_of_rain", 0)
            uvi = hour_data.get("uv", "N/A")
        else:
            # 如果沒有指定活動小時，則取當天的最高值
            max_uv = 0
            max_temp = -999
            max_pop = 0
            desc = None
            for hour_data in forecast_day["hour"]:
                if hour_data["uv"] > max_uv:
                    max_uv = hour_data["uv"]
                if hour_data["temp_c"] > max_temp:
                    max_temp = hour_data["temp_c"]
                    desc = hour_data["condition"]["text"] # 取最高溫時的描述
                if hour_data.get("chance_of_rain", 0) > max_pop:
                    max_pop = hour_data.get("chance_of_rain", 0)
            desc = cc.convert(desc) if desc else "天氣資料不足"
            temp = max_temp
            pop = max_pop
            uvi = max_uv

        return f"{desc}，氣溫 {temp}°C，降雨機率 {pop}% ，紫外線 {uvi}（{interpret_uv_index(uvi)}）"
    except requests.exceptions.Timeout:
        print(f"❌ WeatherAPI 查詢超時：{location_name}")
        return "⚠️ 找不到天氣資料 (查詢超時)"
    except requests.exceptions.RequestException as e:
        print(f"❌ WeatherAPI 查詢失敗（網路或其他請求問題）：{e}")
        return "⚠️ 找不到天氣資料 (網路錯誤)"
    except Exception as e:
        print(f"❌ WeatherAPI 查詢失敗：{e}")
        return "⚠️ 找不到天氣資料 (未知錯誤)"

# --- 發送 LINE 訊息 (已加入錯誤處理) ---
def send_message(msg):
    """向 LINE 群組發送文字訊息。"""
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Authorization': f'Bearer {LINE_TOKEN}',
        'Content-Type': 'application/json'
    }
    payload = {
        'to': GROUP_ID,
        'messages': [{'type': 'text', 'text': msg}]
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10) # 設定 10 秒超時
        print("訊息發送結果：")
        print(f"  狀態碼: {r.status_code}")
        print(f"  回傳內容: {r.text}")
        r.raise_for_status() # 如果狀態碼不是 2xx，會拋出 HTTPError
        print("✅ 訊息已成功發送至 LINE。")
    except requests.exceptions.Timeout:
        print("❌ 訊息發送失敗：請求超時。")
    except requests.exceptions.HTTPError as e:
        print(f"❌ 訊息發送失敗：HTTP 錯誤 {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        print(f"❌ 訊息發送失敗：發生網路錯誤或其他請求問題: {e}")
    except Exception as e:
        print(f"❌ 訊息發送失敗：發生未知錯誤: {e}")

# --- Flask 路由 ---
@app.route("/")
def index():
    return "Bot is running!"

@app.route("/run", methods=["GET"])
def run():
    """主運行函數，獲取日曆事件並發送 LINE 提醒。"""
    events, target_date = get_google_calendar_events()
    # 計算目標日期距離今天的天數偏移，用於天氣查詢
    offset = (target_date - datetime.date.today()).days

    if not events:
        # 如果沒有事件，發送無行程提醒
        send_message(f"【{target_date.strftime('%m/%d')} 行程提醒】\n📭 {target_date.strftime('%m/%d')} 沒有安排外出行程，請好好上班:))")
        return "No events for the target date."

    # 如果有事件，組裝詳細行程訊息
    lines = [f"【{target_date.strftime('%m/%d')} 行程提醒】"]
    for event in events:
        summary = event.get("summary", "（未命名行程）")
        start_info = event.get("start", {})
        location = event.get("location")
        start_time = start_info.get("dateTime") or start_info.get("date")

        try:
            # 判斷是全天活動還是具體時間的活動
            if "T" in start_time:
                dt_obj = datetime.datetime.fromisoformat(start_time)
                time_str = dt_obj.strftime('%H:%M')
                event_hour = dt_obj.hour
            else:
                time_str = "(整天)"
                event_hour = None # 全天活動天氣查詢不指定小時
        except ValueError: # 處理日期時間格式錯誤
            time_str = "(時間錯誤)"
            event_hour = None
            print(f"⚠️ 解析時間錯誤：{start_time}")

        if location:
            coords = geocode_location(location)
            if coords:
                # 嘗試從座標獲取鄉鎮，如果失敗則直接用原始地點名稱查詢天氣
                township = get_township_from_coords(*coords)
                query_location = township or location
                weather_info = fetch_weather_by_weatherapi(query_location, offset, event_hour)
            else:
                weather_info = "⚠️ 無法取得地點座標，跳過天氣查詢。"
            lines.append(f"📌 {time_str}《{summary}》\n📍 地點：{location}\n🌤️ 天氣：{weather_info}\n")
        else:
            lines.append(f"📌 {time_str}《{summary}》（無地點）\n")

    send_message("\n".join(lines))
    return "Checked and sent."

@app.route("/debug", methods=["GET"])
def debug_weather():
    """天氣查詢除錯端點，可指定地點進行測試。"""
    location = request.args.get("location", default="平溪車站")
    coords = geocode_location(location)
    if not coords:
        return f"❌ 找不到地點：{location}"

    township = get_township_from_coords(*coords)
    query_location = township or location # 如果找不到鄉鎮，還是用原始地點名稱查詢

    taiwan_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today_weekday = taiwan_now.weekday()
    offset = 3 if today_weekday == 4 else 1 # 和主邏輯保持一致的日期偏移
    
    # 固定為早上 8 點查詢，方便測試
    weather = fetch_weather_by_weatherapi(query_location, offset, event_hour=8)
    return (
        f"✅ 測試地點：{location}\n"
        f"📍 座標：{coords}\n"
        f"🏙️ 查詢地區：{query_location}\n"
        f"🗓️ 今天星期：{today_weekday} (0=Mon, ..., 4=Fri)\n"
        f"➡️ 預計查詢 {offset} 天後的天氣\n"
        f"🌤️ 天氣：{weather}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
