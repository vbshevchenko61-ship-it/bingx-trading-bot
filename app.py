import os
import threading
import subprocess
import sys
from flask import Flask, jsonify

# Create Flask app for Gunicorn
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Global variable to track bot process
bot_process = None

@app.route('/')
def home():
    return jsonify({
        "status": "BingX Telegram Bot is running!",
        "message": "Send /start to the bot in Telegram to subscribe to trading notifications"
    })

@app.route('/status')
def status():
    global bot_process
    is_running = bot_process and bot_process.poll() is None
    return jsonify({
        "bot_running": is_running,
        "message": "Bot is running" if is_running else "Bot is not running"
    })

def start_telegram_bot():
    """Start the telegram bot in a subprocess"""
    global bot_process
    try:
        bot_process = subprocess.Popen([sys.executable, "telegram_bot.py"])
        print("Telegram bot started successfully")
    except Exception as e:
        print(f"Failed to start telegram bot: {e}")

# Initialize bot when module is imported
bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
bot_thread.start()

if __name__ == '__main__':
    # For direct running
    start_telegram_bot()
    app.run(host='0.0.0.0', port=5000, debug=False)