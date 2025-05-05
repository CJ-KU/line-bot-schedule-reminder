import requests
import json
import os
import logging
import datetime
import time
from dotenv import load_dotenv

load_dotenv()  # 載入 .env 檔案

# 設定日誌記錄
log_file = f"cwa_data_update_{datetime.datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(filename=log_file, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    encoding='utf-8')  # 指定編碼

CWA_API_KEY = os.getenv("CWA_API_KEY")  # 從環境變數獲取 API 金鑰
CWA_API_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-089"
CWA_JSON_PATH = "F-D0047-089.json"
RETRY_INTERVAL = 60  # 重試間隔 (秒)
MAX_RETRIES = 3  # 最大重試次數

def fetch_cwa_data(url, params):
    """從中央氣象署 API 獲取資料，包含重試機制"""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, params=params, timeout=10)  # 增加 timeout
            response.raise_for_status()  # 檢查 HTTP 狀態碼
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                logging.info(f"Retrying in {RETRY_INTERVAL} seconds...")
                time.sleep(RETRY_INTERVAL)
            else:
                logging.error("Max retries reached. Aborting.")
                return None
        except json.JSONDecodeError as e:
            logging.error(f"JSON Decode Error: {e}")
            return None
    return None

def validate_data(data):
    """驗證 API 回應資料的結構 (基本驗證)"""
    if not isinstance(data, dict):
        logging.error("Data is not a dictionary.")
        return False
    if "cwaopendata" not in data or "dataset" not in data["cwaopendata"] or \
       "locations" not in data["cwaopendata"]["dataset"]:
        logging.error("Missing top-level keys in data.")
        return False
    return True

def save_to_json(data, filepath):
    """將資料儲存到 JSON 檔案"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)  # ensure_ascii=False 處理中文
        logging.info(f"Data successfully saved to {filepath}")
        return True
    except Exception as e:
        logging.error(f"Error saving data to JSON: {e}")
        return False

def update_cwa_json():
    """更新 CWA JSON 檔案的主要邏輯"""
    params = {
        "Authorization": CWA_API_KEY,
        "format": "JSON"
    }
    new_data = fetch_cwa_data(CWA_API_URL, params)

    if new_data and validate_data(new_data):
        if save_to_json(new_data, CWA_JSON_PATH):
            logging.info("CWA JSON file updated successfully.")
        else:
            logging.error("Failed to save updated data.")
    else:
        logging.error("Failed to fetch or validate CWA data. JSON file not updated.")

if __name__ == "__main__":
    update_cwa_json()
