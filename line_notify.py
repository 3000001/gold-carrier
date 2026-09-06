import os
import requests

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

PAPA_USER_ID = "U36050f212bd1271eca08a2a238e0e461"


def send_line(message):
    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    data = {
        "to": PAPA_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=15
    )

    print("LINE STATUS:", response.status_code)
    print("LINE RESPONSE:", response.text)

    return response.status_code == 200


if __name__ == "__main__":
    send_line("✅ ทดสอบ Trading Alert\nเชื่อมต่อ LINE ของปาป้าสำเร็จ")