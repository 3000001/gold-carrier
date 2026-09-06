import os
import time
import json
import sqlite3
import hmac
import hashlib
import base64
import threading
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
import pandas as pd
from flask import Flask, request, abort

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")  # ใช้ตรวจสอบ webhook signature
LINE_USER_ID = os.getenv("LINE_USER_ID")

GOLD_SYMBOL = "GC=F"
CHECK_INTERVAL = 60          # ตรวจตลาดทุก 60 วินาที
ALERT_COOLDOWN = 15 * 60     # สัญญาณประเภทเดิมอย่างน้อย 15 นาที
SIDEWAY_THRESHOLD = 0.30     # % ช่วงแกว่ง 15M
PIVOT_WINDOW = 3             # pivot ซ้าย/ขวา 3 แท่ง
ATR_PERIOD = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 2.5

TZ = ZoneInfo("Asia/Bangkok")

DB_PATH = os.getenv("STATE_DB_PATH", "/tmp/gold_alert_state.db")
LOCK_PATH = os.getenv("LOCK_FILE_PATH", "/tmp/gold_alert_bot.lock")

YF_MAX_RETRIES = 3
YF_TIMEOUT_SEC = 15
YF_BACKOFF_BASE = 3  # seconds, doubles each retry

# ============================================================
# PERSISTENT STATE (sqlite) — survives restarts, unlike plain globals
# ============================================================
state_lock = threading.Lock()


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)"
    )
    return conn


def get_state(key, default=None):
    with state_lock:
        conn = _db()
        try:
            row = conn.execute(
                "SELECT value FROM state WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return default
            return json.loads(row[0])
        finally:
            conn.close()


def set_state(key, value):
    with state_lock:
        conn = _db()
        try:
            conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
            conn.commit()
        finally:
            conn.close()


# ============================================================
# SINGLE-INSTANCE GUARD (best-effort)
# Prevents duplicate LINE alerts if the host runs multiple workers
# (e.g. `gunicorn --workers 2`). Only the process that grabs the
# lock file will run the background trading loop.
# ============================================================
def acquire_singleton_lock():
    try:
        import fcntl
        lock_fh = open(LOCK_PATH, "w")
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fh  # keep reference alive, or lock releases
    except (ImportError, OSError):
        # fcntl unavailable (e.g. Windows) or another process holds it
        return None


# ============================================================
# LINE
# ============================================================
def send_line(message):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("❌ ไม่มี LINE_CHANNEL_ACCESS_TOKEN")
        return False
    if not LINE_USER_ID:
        print("❌ ไม่มี LINE_USER_ID")
        return False

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message}],
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        print("LINE:", response.status_code, response.text)
        return response.status_code == 200
    except Exception as e:
        print("❌ LINE ERROR:", e)
        return False


def verify_line_signature(body_bytes, signature_header):
    """ตรวจสอบว่า request มาจาก LINE จริง ป้องกันคนภายนอกยิง webhook ปลอม"""
    if not LINE_CHANNEL_SECRET:
        # ไม่ได้ตั้ง secret ไว้ -> ข้ามการตรวจสอบ (ไม่แนะนำใน production)
        print("⚠️ LINE_CHANNEL_SECRET ไม่ถูกตั้งค่า — ข้ามการตรวจสอบ signature")
        return True
    if not signature_header:
        return False
    hash_digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"), body_bytes, hashlib.sha256
    ).digest()
    expected_signature = base64.b64encode(hash_digest).decode("utf-8")
    return hmac.compare_digest(expected_signature, signature_header)


# ============================================================
# INDICATORS
# ============================================================
def stochastic(df, period=14, smooth=3):
    low_min = df["Low"].rolling(period).min()
    high_max = df["High"].rolling(period).max()
    denominator = high_max - low_min
    denominator = denominator.replace(0, float("nan"))
    k = 100 * ((df["Close"] - low_min) / denominator)
    d = k.rolling(smooth).mean()
    return k, d


def rsi(df, period=14):
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    return result


def atr(df, period=ATR_PERIOD):
    high = df["High"]
    low = df["Low"]
    close_prev = df["Close"].shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


# ============================================================
# RSI DIVERGENCE
# ============================================================
def find_pivots(series, window=PIVOT_WINDOW):
    lows = []
    highs = []
    values = series.to_numpy()
    for i in range(window, len(values) - window):
        value = values[i]
        if pd.isna(value):
            continue
        left = values[i - window:i]
        right = values[i + 1:i + window + 1]
        if value <= min(left) and value <= min(right):
            lows.append(i)
        if value >= max(left) and value >= max(right):
            highs.append(i)
    return lows, highs


def detect_rsi_divergence(df, lookback=100):
    """
    Bullish divergence: Price ทำ Lower Low, RSI ทำ Higher Low
    Bearish divergence: Price ทำ Higher High, RSI ทำ Lower High
    """
    if len(df) < 40:
        return False, False

    data = df.tail(lookback).copy()
    price_lows, _ = find_pivots(data["Low"])
    _, price_highs = find_pivots(data["High"])

    bullish = False
    bearish = False

    if len(price_lows) >= 2:
        p1, p2 = price_lows[-2], price_lows[-1]
        price1 = float(data["Low"].iloc[p1])
        price2 = float(data["Low"].iloc[p2])
        rsi1 = float(data["RSI"].iloc[p1])
        rsi2 = float(data["RSI"].iloc[p2])
        if price2 < price1 and rsi2 > rsi1 and rsi1 < 50 and rsi2 < 55:
            bullish = True

    if len(price_highs) >= 2:
        p1, p2 = price_highs[-2], price_highs[-1]
        price1 = float(data["High"].iloc[p1])
        price2 = float(data["High"].iloc[p2])
        rsi1 = float(data["RSI"].iloc[p1])
        rsi2 = float(data["RSI"].iloc[p2])
        if price2 > price1 and rsi2 < rsi1 and rsi1 > 50 and rsi2 > 45:
            bearish = True

    return bullish, bearish


# ============================================================
# MARKET DATA (with retry/backoff + timeout)
# ============================================================
def clean_yfinance(df):
    if df is None or df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Open", "High", "Low", "Close"]).copy()


def download_with_retry(symbol, period, interval):
    last_error = None
    for attempt in range(1, YF_MAX_RETRIES + 1):
        try:
            df = yf.download(
                symbol,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=False,
                threads=False,
                timeout=YF_TIMEOUT_SEC,
            )
            df = clean_yfinance(df)
            if df is not None and not df.empty:
                return df
            last_error = "empty dataframe"
        except Exception as e:
            last_error = e

        if attempt < YF_MAX_RETRIES:
            wait = YF_BACKOFF_BASE * (2 ** (attempt - 1))
            print(
                f"⚠️ yfinance ดึงข้อมูล {symbol} {interval} ล้มเหลว "
                f"(ครั้งที่ {attempt}/{YF_MAX_RETRIES}): {last_error} "
                f"— รอ {wait}s แล้วลองใหม่"
            )
            time.sleep(wait)

    print(f"❌ ดึงข้อมูล {symbol} {interval} ไม่สำเร็จหลังลอง {YF_MAX_RETRIES} ครั้ง: {last_error}")
    return None


def get_gold_data():
    df1 = download_with_retry(GOLD_SYMBOL, period="1d", interval="1m")
    df15 = download_with_retry(GOLD_SYMBOL, period="5d", interval="15m")
    daily = download_with_retry(GOLD_SYMBOL, period="6mo", interval="1d")

    if df1 is None or df15 is None or daily is None:
        return None, None, None

    return df1, df15, daily


# ============================================================
# ALERT CONTROL (backed by persistent state)
# ============================================================
def should_send_signal(signal_type):
    now = time.time()
    last_signal_type = get_state("last_signal_type")
    last_signal_time = get_state("last_signal_time", 0)

    if signal_type != last_signal_type:
        set_state("last_signal_type", signal_type)
        set_state("last_signal_time", now)
        return True

    if now - last_signal_time >= ALERT_COOLDOWN:
        set_state("last_signal_time", now)
        return True

    return False


# ============================================================
# ANALYSIS
# ============================================================
def analyze_gold():
    try:
        df1, df15, daily = get_gold_data()
        if df1 is None:
            print("❌ ดึงข้อมูล GOLD ไม่สำเร็จ ข้ามรอบนี้")
            return

        # ------------------------------------------------
        # Indicators
        # ------------------------------------------------
        df1["K"], df1["D"] = stochastic(df1)
        df1["RSI"] = rsi(df1)

        df15["K"], df15["D"] = stochastic(df15)
        df15["RSI"] = rsi(df15)
        df15["ATR"] = atr(df15)

        daily["K"], daily["D"] = stochastic(daily)
        daily["RSI"] = rsi(daily)

        df1 = df1.dropna(subset=["K", "D", "RSI"])
        df15 = df15.dropna(subset=["K", "D", "RSI", "ATR"])
        daily = daily.dropna(subset=["K", "D", "RSI"])

        if len(df1) < 30 or len(df15) < 30 or len(daily) < 20:
            print("⚠️ ข้อมูล Indicator ยังไม่เพียงพอ")
            return

        # ------------------------------------------------
        # Current values
        # ------------------------------------------------
        price = float(df1["Close"].iloc[-1])
        k1 = float(df1["K"].iloc[-1])
        d1 = float(df1["D"].iloc[-1])
        rsi1 = float(df1["RSI"].iloc[-1])
        k1_prev = float(df1["K"].iloc[-2])
        d1_prev = float(df1["D"].iloc[-2])

        k15 = float(df15["K"].iloc[-1])
        d15 = float(df15["D"].iloc[-1])
        rsi15 = float(df15["RSI"].iloc[-1])
        atr15 = float(df15["ATR"].iloc[-1])

        kd = float(daily["K"].iloc[-1])
        dd = float(daily["D"].iloc[-1])
        rsi_daily = float(daily["RSI"].iloc[-1])

        bullish_div, bearish_div = detect_rsi_divergence(df15)

        # ------------------------------------------------
        # SIDEWAY FILTER
        # ------------------------------------------------
        recent = df15.tail(12)
        recent_high = float(recent["High"].max())
        recent_low = float(recent["Low"].min())
        movement_percent = ((recent_high - recent_low) / price) * 100
        sideways = movement_percent < SIDEWAY_THRESHOLD

        # ------------------------------------------------
        # 1M CROSS
        # ------------------------------------------------
        bullish_cross_1m = k1_prev <= d1_prev and k1 > d1
        bearish_cross_1m = k1_prev >= d1_prev and k1 < d1

        # ------------------------------------------------
        # DAILY BIAS
        # ------------------------------------------------
        daily_buy_bias = kd < 30 and kd > dd
        daily_sell_bias = kd > 70 and kd < dd

        daily_state = "NEUTRAL"
        if daily_buy_bias:
            daily_state = "BUY"
        elif daily_sell_bias:
            daily_state = "SELL"

        last_daily_state = get_state("last_daily_state")
        if daily_state != last_daily_state:
            if daily_state == "BUY":
                send_line(
                    "🟢 GOLD DAILY BIAS\n\n"
                    f"💰 ราคา: {price:.2f}\n"
                    f"Daily Stoch K: {kd:.1f}\n"
                    f"Daily Stoch D: {dd:.1f}\n"
                    f"Daily RSI: {rsi_daily:.1f}\n\n"
                    "📈 Daily เริ่มให้น้ำหนักฝั่ง BUY\n"
                    "⚠️ ระวังการ SELL สวนแนวโน้ม"
                )
            elif daily_state == "SELL":
                send_line(
                    "🔴 GOLD DAILY BIAS\n\n"
                    f"💰 ราคา: {price:.2f}\n"
                    f"Daily Stoch K: {kd:.1f}\n"
                    f"Daily Stoch D: {dd:.1f}\n"
                    f"Daily RSI: {rsi_daily:.1f}\n\n"
                    "📉 Daily เริ่มให้น้ำหนักฝั่ง SELL\n"
                    "⚠️ ระวังการ BUY สวนแนวโน้ม"
                )
            set_state("last_daily_state", daily_state)

        # ------------------------------------------------
        # DETERMINE SIGNAL
        # ------------------------------------------------
        signal_type = "NORMAL"
        message = None

        if sideways:
            signal_type = "SIDEWAY"
            message = (
                "🚫 GOLD SIDEWAY\n\n"
                f"💰 ราคา: {price:.2f}\n"
                f"ช่วงแกว่ง 15M: {movement_percent:.2f}%\n\n"
                "ตลาดยังไม่มี Trend ชัดเจน\n"
                "⛔ ยังไม่เข้า รอสัญญาณใหม่"
            )

        elif k15 < 25 and rsi15 < 45:
            signal_type = "PREPARE_BUY"
            div_text = (
                "✅ พบ Bullish RSI Divergence"
                if bullish_div
                else "⏳ ยังไม่พบ Bullish RSI Divergence"
            )
            message = (
                "🟡 GOLD เตรียมรอ BUY\n\n"
                f"💰 ราคา: {price:.2f}\n"
                f"15M Stoch K: {k15:.1f}\n"
                f"15M RSI: {rsi15:.1f}\n"
                f"{div_text}\n\n"
                "กำลังอยู่ในโซนเฝ้าระวัง BUY\n"
                "รอ 1M ยืนยันก่อนเข้า"
            )

            if bullish_cross_1m and k1 < 35 and rsi1 >= 30 and bullish_div:
                signal_type = "BUY"
                sl = price - atr15 * ATR_SL_MULT
                tp = price + atr15 * ATR_TP_MULT
                message = (
                    "🟢 GOLD BUY SIGNAL\n\n"
                    f"💰 BUY บริเวณ: {price:.2f}\n"
                    f"🛑 SL (ATR15 x{ATR_SL_MULT}): {sl:.2f}\n"
                    f"🎯 TP (ATR15 x{ATR_TP_MULT}): {tp:.2f}\n\n"
                    f"1M Stoch K/D: {k1:.1f}/{d1:.1f}\n"
                    f"1M RSI: {rsi1:.1f}\n"
                    f"15M Stoch K: {k15:.1f}\n"
                    f"15M RSI: {rsi15:.1f}\n\n"
                    "✅ 15M อยู่โซน BUY\n"
                    "✅ Bullish RSI Divergence\n"
                    "✅ 1M Stochastic ตัดขึ้น\n\n"
                    "📈 เงื่อนไข BUY ผ่าน\n"
                    "⚠️ SL/TP คำนวณจาก ATR อัตโนมัติ โปรดตรวจสอบก่อนเข้าออเดอร์จริง"
                )

        elif k15 > 75 and rsi15 > 55:
            signal_type = "PREPARE_SELL"
            div_text = (
                "✅ พบ Bearish RSI Divergence"
                if bearish_div
                else "⏳ ยังไม่พบ Bearish RSI Divergence"
            )
            message = (
                "🟠 GOLD เตรียมรอ SELL\n\n"
                f"💰 ราคา: {price:.2f}\n"
                f"15M Stoch K: {k15:.1f}\n"
                f"15M RSI: {rsi15:.1f}\n"
                f"{div_text}\n\n"
                "กำลังอยู่ในโซนเฝ้าระวัง SELL\n"
                "รอ 1M ยืนยันก่อนเข้า"
            )

            if bearish_cross_1m and k1 > 65 and rsi1 <= 70 and bearish_div:
                signal_type = "SELL"
                sl = price + atr15 * ATR_SL_MULT
                tp = price - atr15 * ATR_TP_MULT
                message = (
                    "🔴 GOLD SELL SIGNAL\n\n"
                    f"💰 SELL บริเวณ: {price:.2f}\n"
                    f"🛑 SL (ATR15 x{ATR_SL_MULT}): {sl:.2f}\n"
                    f"🎯 TP (ATR15 x{ATR_TP_MULT}): {tp:.2f}\n\n"
                    f"1M Stoch K/D: {k1:.1f}/{d1:.1f}\n"
                    f"1M RSI: {rsi1:.1f}\n"
                    f"15M Stoch K: {k15:.1f}\n"
                    f"15M RSI: {rsi15:.1f}\n\n"
                    "✅ 15M อยู่โซน SELL\n"
                    "✅ Bearish RSI Divergence\n"
                    "✅ 1M Stochastic ตัดลง\n\n"
                    "📉 เงื่อนไข SELL ผ่าน\n"
                    "⚠️ SL/TP คำนวณจาก ATR อัตโนมัติ โปรดตรวจสอบก่อนเข้าออเดอร์จริง"
                )

        # ------------------------------------------------
        # SEND ALERT
        # ------------------------------------------------
        if signal_type != "NORMAL" and message and should_send_signal(signal_type):
            send_line(message)

        if signal_type == "NORMAL":
            set_state("last_signal_type", None)

        # ------------------------------------------------
        # LOG
        # ------------------------------------------------
        now_text = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        print(
            f"[{now_text}] "
            f"GOLD={price:.2f} | "
            f"15M K={k15:.1f} D={d15:.1f} RSI={rsi15:.1f} | "
            f"1M K={k1:.1f} D={d1:.1f} RSI={rsi1:.1f} | "
            f"BULL_DIV={bullish_div} | "
            f"BEAR_DIV={bearish_div} | "
            f"SIDEWAY={sideways} | "
            f"SIGNAL={signal_type}"
        )

    except Exception as e:
        print("❌ ANALYZE ERROR:", e)
        traceback.print_exc()


# ============================================================
# TRADING LOOP
# ============================================================
def trading_loop():
    print("🚀 GOLD TRADING ALERT STARTED")
    time.sleep(10)  # รอ Flask/Render เริ่มก่อน
    while True:
        try:
            analyze_gold()
        except Exception as e:
            print("❌ LOOP ERROR:", e)
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL)


# ============================================================
# WEB SERVER
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return "Gold Trading Alert is running", 200


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "Gold Trading Alert"}, 200


# ============================================================
# LINE WEBHOOK (with signature verification)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    body_bytes = request.get_data()
    signature = request.headers.get("X-Line-Signature")

    if not verify_line_signature(body_bytes, signature):
        print("❌ Webhook signature ไม่ถูกต้อง — ปฏิเสธ request")
        abort(403)

    body = request.get_json(silent=True)
    print("Webhook:", body)

    try:
        events = body.get("events", []) if body else []
        for event in events:
            source = event.get("source", {})
            user_id = source.get("userId")
            if user_id:
                print("👤 LINE USER ID:", user_id)
    except Exception as e:
        print("Webhook parse error:", e)

    return "OK", 200


# ============================================================
# START BACKGROUND THREAD (only in the process holding the lock,
# to avoid duplicate alerts under multi-worker deployments)
# ============================================================
_singleton_lock_fh = acquire_singleton_lock()

if _singleton_lock_fh is not None:
    trading_thread = threading.Thread(target=trading_loop, daemon=True)
    trading_thread.start()
else:
    print(
        "ℹ️ อีก process หนึ่งกำลังรัน trading loop อยู่แล้ว "
        "(หรือระบบไม่รองรับ file lock) — ข้ามการเริ่ม thread ในโปรเซสนี้ "
        "เพื่อป้องกันการแจ้งเตือนซ้ำ"
    )

# ============================================================
# LOCAL START
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
