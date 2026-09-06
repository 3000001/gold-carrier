from flask import Flask, request

app = Flask(__name__)

@app.route("/test-line", methods=["GET"])
def test_line():
    ok = send_line("✅ ทดสอบ Trading Alert\nเชื่อมต่อ LINE ของปาป้าสำเร็จ")

    if ok:
        return "LINE SENT", 200
    else:
        return "LINE FAILED", 500

    print("FULL EVENT", data, flush=True)
    events = data.get("events", [])

    for event in events:
        source = event.get("source", {})
        user_id = source.get("userId")
        print ("USER ID:", user_id)

    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)