from flask import Flask, request

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    print("FULL EVENT", data, flush=True)
    events = data.get("events", [])

    for event in events:
        source = event.get("source", {})
        user_id = source.get("userId")
        print ("USER ID:", user_id)

    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)