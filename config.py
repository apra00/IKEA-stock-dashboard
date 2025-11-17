import os
from dotenv import load_dotenv


load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret_to_something")
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("DATABASE_URL")
        or f"sqlite:///{os.path.join(BASE_DIR, 'ikea_availability.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # SMTP / email settings
    SMTP_SERVER = os.environ.get("SMTP_SERVER", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")

    # Webhook API key (admin can see / manage this value outside the app)
    WEBHOOK_API_KEY = os.environ.get("WEBHOOK_API_KEY", "change-this-secret_to_something")


class DevConfig(Config):
    DEBUG = True


config = {
    "default": DevConfig,
    "development": DevConfig,
}
