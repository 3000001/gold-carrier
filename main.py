import os
import time
import threading
import requests
import yfinance as yf
import pandas as pd
from flask import Flask, request

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

GOLD_SYMBOL = "GC=F"

last_alert = ""


# =========================
# LINE
# =========================

def send_line(message):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("❌ LINE TOKEN หรือ USER ID ไม่มี")
        return

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "to": LINE_USER_ID,
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
        timeout=20
    )

    print("LINE:", response.status_code, response.text)


# =========================
# INDICATORS
# =========================

def stochastic(df, period=14):
    low_min = df["Low"].rolling(period).min()
    high_max = df["High"].rolling(period).max()

    k = 100 * (
        (df["Close"] - low_min) /
        (high_max - low_min)
    )

    d = k.rolling(3).mean()

    return k, d


def rsi(df, period=14):
    delta = df["Close"].diff()

    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()

    rs = gain / loss

    return 100 - (100 / (1 + rs))


# =========================
# MARKET DATA
# =========================

def get_gold_data():

    df1 = yf.download(
        GOLD_SYMBOL,
        period="1d",
        interval="1m",
        progress=False
    )

    df15 = yf.download(
        GOLD_SYMBOL,
        period="5d",
        interval="15m",
        progress=False
    )

    daily = yf.download(
        GOLD_SYMBOL,
        period="3mo",
        interval="1d",
        progress=False
    )

    if df1.empty or df15.empty or daily.empty:
        return None, None, None

    # yfinance บางเวอร์ชันคืน MultiIndex
    if isinstance(df1.columns, pd.MultiIndex):
        df1.columns = df1.columns.get_level_values(0)

    if isinstance(df15.columns, pd.MultiIndex):
        df15.columns = df15.columns.get_level_values(0)

    if isinstance(daily.columns, pd.MultiIndex):
        daily.columns = daily.columns.get_level_values(0)

    return df1, df15, daily


# =========================
# ANALYSIS
# =========================

def analyze_gold():

    global last_alert

    try:

        df1, df15, daily = get_gold_data()

        if df1 is None:
            print("❌ ดึงข้อมูล Gold ไม่ได้")
            return

        # Indicators
        df1["K"], df1["D"] = stochastic(df1)
        df1["RSI"] = rsi(df1)

        df15["K"], df15["D"] = stochastic(df15)
        df15["RSI"] = rsi(df15)

        daily["K"], daily["D"] = stochastic(daily)

        price = float(df1["Close"].iloc[-1])

        k1 = float(df1["K"].iloc[-1])
        d1 = float(df1["D"].iloc[-1])
        rsi1 = float(df1["RSI"].iloc[-1])

        k15 = float(df15["K"].iloc[-1])
        d15 = float(df15["D"].iloc[-1])
        rsi15 = float(df15["RSI"].iloc[-1])

        kd = float(daily["K"].iloc[-1])
        dd = float(daily["D"].iloc[-1])

        # =========================
        # SIDEWAY FILTER
        # =========================

        recent_high = float(df15["High"].tail(12).max())
        recent_low = float(df15["Low"].tail(12).min())

        movement_percent = (
            (recent_high - recent_low) / price
        ) * 100

        sideways = movement_percent < 0.30

        signal = None

        # =========================
        # SIDEWAY
        # =========================

        if sideways:

            signal = (
                "🚫 GOLD SIDEWAY\n\n"
                f"ราคา: {price:.2f}\n"
                "ตลาดยังไม่มี Trend ชัดเจน\n"
                "⛔ ปาป้ายังไม่เข้า"
            )

        # =========================
        # PREPARE BUY
        # =========================

        elif k15 < 20 and rsi15 < 40:

            signal = (
                "⚠️ GOLD เตรียมรอ BUY\n\n"
                f"💰 ราคา: {price:.2f}\n"
                f"15M Stochastic: {k15:.1f}\n"
                f"15M RSI: {rsi15:.1f}\n\n"
                "กำลังเข้าเขต Oversold\n"
                "รอจังหวะยืนยันจาก 1 นาที"
            )

            # BUY ENTRY
            if (
                k1 > d1
                and k1 < 30
                and rsi1 > 30
            ):

                signal = (
                    "✅ GOLD BUY SIGNAL\n\n"
                    f"💰 BUY แถว: {price:.2f}\n"
                    f"1M Stochastic: {k1:.1f}\n"
                    f"1M RSI: {rsi1:.1f}\n"
                    f"15M Stochastic: {k15:.1f}\n\n"
                    "📈 จังหวะ BUY ผ่านเงื่อนไขเบื้องต้น"
                )

        # =========================
        # PREPARE SELL
        # =========================

        elif k15 > 80 and rsi15 > 60:

            signal = (
                "⚠️ GOLD เตรียมรอ SELL\n\n"
                f"💰 ราคา: {price:.2f}\n"
                f"15M Stochastic: {k15:.1f}\n"
                f"15M RSI: {rsi15:.1f}\n\n"
                "กำลังเข้าเขต Overbought\n"
                "รอจังหวะยืนยันจาก 1 นาที"
            )

            # SELL ENTRY
            if (
                k1 < d1
                and k1 > 70
                and rsi1 < 70
            ):

                signal = (
                    "🔴 GOLD SELL SIGNAL\n\n"
                    f"💰 SELL แถว: {price:.2f}\n"
                    f"1M Stochastic: {k1:.1f}\n"
                    f"1M RSI: {rsi1:.1f}\n"
                    f"15M Stochastic: {k15:.1f}\n\n"
                    "📉 จังหวะ SELL ผ่านเงื่อนไขเบื้องต้น"
                )

        # =========================
        # DAILY BUY BIAS
        # =========================

        if kd < 25 and kd > dd:

            daily_message = (
                "🟢 GOLD DAILY ALERT\n\n"
                "Daily Stochastic อยู่โซนต่ำและเริ่มหันขึ้น\n"
                "📈 วันนี้ให้น้ำหนักฝั่ง BUY\n"
                "⚠️ ระวังการ SELL สวน"
            )

            if last_alert != daily_message:
                send_line(daily_message)
                last_alert = daily_message

        # ป้องกันข้อความเดิมยิงซ้ำทุกนาที
        if signal and signal != last_alert:
            send_line(signal)
            last_alert = signal

        print(
            f"GOLD {price:.2f} | "
            f"15M K={k15:.1f} RSI={rsi15:.1f} | "
            f"1M K={k1:.1f} RSI={rsi1:.1f}"
        )

    except Exception as e:
        print("ERROR:", e)


# =========================
# LOOP
# =========================

def trading_loop():

    print("🚀 Gold Trading Alert Started")

    time.sleep(10)

    while True:

        analyze_gold()

        # ตรวจทุก 60 วินาที
        time.sleep(60)


# =========================
# WEBHOOK
# =========================

@app.route("/", methods=["GET"])
def home():
    return "Gold Trading Alert is running", 200


@app.route("/webhook", methods=["POST"])
def webhook():

    body = request.get_json(silent=True)

    print("Webhook:", body)

    return "OK", 200


# =========================
# START
# =========================

threading.Thread(
    target=trading_loop,
    daemon=True
).start()


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )