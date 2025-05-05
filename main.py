from flask import Flask, request
import datetime
import requests
import os
from dotenv import load_dotenv
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

from lunarcalendar import Converter, Solar

load_dotenv()

app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
SERVICE_ACCOUNT_FILE = 'credentials.json'
CALENDAR_ID = os.getenv("CALENDAR_ID") or 'primary'

def get_google_calendar_events():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)

    now = datetime.datetime.utcnow()
    tomorrow = now + datetime.timedelta(days=1)
    start = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0).isoformat() + 'Z'
    end = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59).isoformat() + 'Z'

    events_result = service.events().list(calendarId=CALENDAR_ID, timeMin=start, timeMax=end,
                                          singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])

    return events

def mock_weather_for_location(location):
    return "晴，高溫 28°C，降雨 20%，紫外線：高"

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

    message_lines = ["【明日行程提醒】"]
    for event in events:
        summary = event.get("summary", "（未命名行程）")
        start = event.get("start", {}).get("dateTime", "")
        time_str = ""
        if start:
            time_str = datetime.datetime.fromisoformat(start).strftime('%H:%M')
        location = event.get("location")
        if location:
            weather_info = mock_weather_for_location(location)
            message_lines.append(f"- {time_str} {summary}\n  地點：{location}\n  天氣：{weather_info}")
        else:
            message_lines.append(f"- {time_str} {summary}（無地點）")

    send_message("\n".join(message_lines))
    return "Checked and sent if needed."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
