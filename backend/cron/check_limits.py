#!/usr/bin/env python
import requests
import os
from datetime import datetime

# Токен для внутренних вызовов (должен совпадать с тем, что в main.py)
INTERNAL_TOKEN = "internal-cron-token"

def check_limits():
    try:
        response = requests.post(
            "http://localhost:8000/api/cron/check-limits",
            headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
            timeout=30
        )
        if response.ok:
            data = response.json()
            print(f"[{datetime.now()}] Checked limits, deactivated: {data.get('deactivated', [])}")
        else:
            print(f"[{datetime.now()}] Error: {response.status_code}")
    except Exception as e:
        print(f"[{datetime.now()}] Exception: {e}")

def collect_traffic():
    # Триггерим сбор трафика (он сам обновит БД и проверит лимиты)
    try:
        response = requests.get(
            "http://localhost:8000/api/traffic",
            headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"}
        )
        if response.ok:
            print(f"[{datetime.now()}] Traffic collected")
    except Exception as e:
        print(f"[{datetime.now()}] Traffic error: {e}")

if __name__ == "__main__":
    collect_traffic()
    check_limits()
