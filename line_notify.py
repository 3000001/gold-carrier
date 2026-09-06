import requests

CHANNEL_ACCESS_TOKEN = "MmxmSJrLjrZE4tLJsAI1By4S0lfCqk3+AfqC9JzJv018TT1C57nNTQ95fy2qtK5B3qxIwIpKV1lWHdVsXSsPs3gwzLE8MyH93t0nAxHLK8hvR/+d4FfNrkfxUz9uwyB6TCkGFYt38qHi+ew7CNJxngdB04t89/1O/w1cDnyilFU="

def send_line_message(user_id, message):
    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }

    data = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }

    response = requests.post(url, headers=headers, json=data)

    print("LINE Status:", response.status_code)
    print("LINE Response:", response.text)