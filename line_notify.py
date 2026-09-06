import os
import requests


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

LINE_USER_IDS = [
    user_id.strip()
    for user_id in os.getenv("LINE_USER_IDS", "").split(",")
    if user_id.strip()
]


def send_line(message):
    """
    ส่งข้อความ LINE ไปยัง User ID ทุกคนที่กำหนดไว้ใน LINE_USER_IDS
    """

    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("❌ ไม่มี LINE_CHANNEL_ACCESS_TOKEN", flush=True)
        return False

    if not LINE_USER_IDS:
        print("❌ ไม่มี LINE_USER_IDS", flush=True)
        return False

    if not message:
        print("❌ ไม่มีข้อความที่จะส่ง", flush=True)
        return False

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    all_ok = True

    for user_id in LINE_USER_IDS:
        data = {
            "to": user_id,
            "messages": [
                {
                    "type": "text",
                    "text": str(message),
                }
            ],
            "notificationDisabled": False,
        }

        try:
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=20,
            )

            print(
                f"LINE -> {user_id[:6]}... :",
                response.status_code,
                response.text,
                flush=True,
            )

            if response.status_code != 200:
                all_ok = False

        except requests.RequestException as e:
            print(
                f"❌ LINE ERROR -> {user_id[:6]}... : {e}",
                flush=True,
            )
            all_ok = False

        except Exception as e:
            print(
                f"❌ UNEXPECTED LINE ERROR -> {user_id[:6]}... : {e}",
                flush=True,
            )
            all_ok = False

    return all_ok


if __name__ == "__main__":
    print(f"👥 LINE recipients configured: {len(LINE_USER_IDS)}", flush=True)

    send_line(
        "🔔 ทดสอบ Trading Alert\n"
        "เปิดการแจ้งเตือน LINE แล้ว"
    )
