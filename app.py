from flask import Flask, request

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    print("ข้อมูลจาก LINE:")
    print(data)

    events = data.get("events", [])

    for event in events:
        source = event.get("source", {})
        user_id = source.get("userId")

        if user_id:
            print("USER ID ของปาป้า =", user_id)

    return "OK", 200


if __name__ == "__main__":
    app.run(port=5000)