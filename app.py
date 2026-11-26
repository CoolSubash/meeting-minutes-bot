"""Entry point for gunicorn (`gunicorn app:app`) and `python app.py`.

The application lives in the minutes_bot package.
"""
import os

from minutes_bot.app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")))
