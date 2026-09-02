from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def health():
    return "Bot is running! 🤖"

def run_flask():
    app.run(host='0.0.0.0', port=10000)

thread = threading.Thread(target=run_flask)
thread.daemon = True
thread.start()

print("✅ Health check server started on port 10000")