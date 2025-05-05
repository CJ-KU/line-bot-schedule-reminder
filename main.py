import os
import json
import datetime
import requests
from urllib.parse import quote
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

CWA_API_KEY = os.getenv("CWA_API_KEY")


def fetch_tomorrow_weather_by_cwa(location_name):
    try:
        print(f"\U0001F4E1 嘗試從中央氣象署取得明日預報：{location_name}")
        encoded_location = quote(location_name)
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089?Authorization={CWA_API_KEY}&locationName={encoded_location}"
        res = requests.get(url, timeout=10)
        data = res.json()

        if res.status_code != 200 or not data.get("records"):
            return None

        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).date()

        location = data["records"]["locations"][0]["location"][0]
        weather_elements = location["weatherElement"]

        def extract_avg(element_name):
            for e in weather_elements:
                if e["elementName"] == element_name:
                    values = []
                    for t in e["time"]:
                        time_str = t.get("dataTime") or t.get("startTime")
                        if not time_str:
                            continue
                        time_obj = datetime.datetime.fromisoformat(time_str)
                        if time_obj.date() == tomorrow and 6 <= time_obj.hour <= 18:
                            val = t["elementValue"][0].get("value") or t["elementValue"][0].get(element_name)
                            try:
                                values.append(float(val))
                            except:
                                continue
                    return round(sum(values) / len(values)) if values else "-"
            return "-"

        temperature = extract_avg("溫度")
        apparent_temp = extract_avg("體感溫度")
        rain_prob = extract_avg("降雨機率")

        return f"\U0001F321️ 溫度 {temperature}°C，體感 {apparent_temp}°C，\u2614 降雨機率 {rain_prob}%"

    except Exception as e:
        print("❌ fetch_tomorrow_weather_by_cwa 失敗：", e)
        return None


@app.route("/debug", methods=["GET"])
def debug():
    location = request.args.get("location", default="信義區")
    result = fetch_tomorrow_weather_by_cwa(location)
    if result:
        return f"✅ 測試地點：{location}\n🌤️ 天氣：{result}"
    else:
        return f"⚠️ 無法取得 {location} 明日天氣資料"


if __name__ == "__main__":
    app.run(debug=True, port=5000)
