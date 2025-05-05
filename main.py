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

# 模擬天氣
def mock_weather_for_location(location):
    return "晴，高溫 28°C，降雨 20%，紫外線：高"

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

        # 處理整天與非整天活動
        start_time = start_info.get("dateTime") or start_info.get("date")
        if "T" in start_time:
            time_str = datetime.datetime.fromisoformat(start_time).strftime('%H:%M')
        else:
            time_str = "(整天)"

        if location:
            weather_info = mock_weather_for_location(location)
            message_lines.append(f"- {time_str} {summary}\n  地點：{location}\n  天氣：{weather_info}")
        else:
            message_lines.append(f"- {time_str} {summary}（無地點）")

    send_message("\n".join(message_lines))
    return "Checked and sent if needed."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
