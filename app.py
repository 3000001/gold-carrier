import os
import hmac
import hashlib
import base64

from flask import Flask, request, abort
from line_notify import send_line

app = Flask(__name__)

# ต้องตรงกับ LINE_CHANNEL_SECRET ที่ตั้งไว้ใน Render / LINE Developers
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")


def verify_line_signature(body_bytes, signature_header):
    """ตรวจสอบว่า request มาจาก LINE จริง"""
    if not LINE_CHANNEL_SECRET:
        print("⚠️ LINE_CHANNEL_SECRET ไม่ถูกตั้งค่า — ข้ามการตรวจสอบ signature")
        return True

    if not signature_header:
        return False

    hash_digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).digest()

    expected_signature = base64.b64encode(hash_digest).decode("utf-8")

    return hmac.compare_digest(
        expected_signature,
        signature_header,
    )


@app.route("/", methods=["GET"])
def home():
    return "LINE test/webhook service is running", 200


@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "ok",
        "service": "LINE test/webhook",
    }, 200


@app.route("/test-line", methods=["GET"])
def test_line():
    """
    ทดสอบส่ง LINE
    ถ้า line_notify.py รองรับ LINE_USER_IDS แล้ว
    ข้อความนี้จะถูกส่งไปทุก User ID
    """
    ok = send_line(
        "✅ ทดสอบ Trading Alert\n"
        "เชื่อมต่อ LINE สำเร็จ"
    )

    if ok:
        return "LINE SENT", 200

    return "LINE FAILED", 500


@app.route("/webhook", methods=["POST"])
def webhook():
    body_bytes = request.get_data()
    signature = request.headers.get("X-Line-Signature")

    if not verify_line_signature(body_bytes, signature):
        print("❌ Webhook signature ไม่ถูกต้อง — ปฏิเสธ request")
        abort(403)

    data = request.get_json(silent=True)

    print("FULL EVENT", data, flush=True)

    events = data.get("events", []) if data else []

    for event in events:
        source = event.get("source", {})
        user_id = source.get("userId")

        if user_id:
            print("👤 LINE USER ID:", user_id, flush=True)

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
    )
