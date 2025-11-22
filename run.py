# run.py
import os
from dotenv import load_dotenv
from app import create_app


load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = create_app(os.environ.get("FLASK_CONFIG", "default"))

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=5000, debug=debug)