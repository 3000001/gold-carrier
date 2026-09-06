from flask import Flask, request

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    print("===== LINE WEBHOOK =====", flush=True)
    print(data, flush=True)

    if data and "events" in data:
        for event in data["events"]:
            source = event.get("source", {})
            user_id = source.get("userId")

            print("USER ID =", user_id, flush=True)

    return "OK", 200