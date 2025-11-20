import os
from dotenv import load_dotenv


load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY must be set in environment for security.")
    
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

    SESSION_COOKIE_SECURE = True       # only over HTTPS
    SESSION_COOKIE_HTTPONLY = True     # not accessible from JS
    SESSION_COOKIE_SAMESITE = "Lax"    # or "Strict" if OK for you
    REMEMBER_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours, or use timedelta

    def validate(cls):
        if not cls.SECRET_KEY:
            raise RuntimeError("SECRET_KEY must be set.")
        # Treat missing ADMIN_API_KEY as configuration error if webhooks are enabled
        # (or at least log a big warning)


class DevConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


config = {
    "default": Config,
    "development": DevConfig,
}
