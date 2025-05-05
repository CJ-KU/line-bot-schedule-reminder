# Lunar Line Bot

每天 17:30 自動提醒 Google Calendar 明日行程，並附上天氣資訊（若有地點）。

## 使用方式

1. 上傳 `credentials.json` 憑證檔（Google Cloud）
2. 設定 `.env` 內容：
   - LINE_TOKEN=
   - GROUP_ID=
   - CALENDAR_ID=primary（或自訂）

## 路由

- `/` → 狀態確認
- `/run` → 執行明日行程提醒
