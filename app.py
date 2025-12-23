# app.py
import asyncio
import threading
from flask import Flask
from main import main


app = Flask(__name__)

# Run the bot in a separate thread
def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

threading.Thread(target=start_bot, daemon=True).start()

@app.route('/')
def health():
    return "Bot is running", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
