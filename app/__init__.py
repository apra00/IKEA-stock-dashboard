from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .extensions import db, login_manager, csrf, limiter
from .models import User
from .models import create_default_admin
from .dashboard.routes import dashboard_bp
from .auth.routes import auth_bp
from .items.routes import items_bp
from .users.routes import users_bp
from .api.routes import api_bp
from config import config
from flask_migrate import Migrate

migrate = Migrate()


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__)

    # Load config class
    cfg_cls = config[config_name]
    app.config.from_object(cfg_cls)

    # --- IMPORTANT FOR APACHE/REVERSE PROXY ---
    # Trust proxy headers (X-Forwarded-For, Proto, Host, Port, Prefix)
    # so Flask sees real client IP + https scheme.
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
        x_prefix=1,
    )

    # Init extensions
    limiter.init_app(app)
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    login_manager.login_view = "auth.login"

    # Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(items_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(api_bp)

    # Initialize DB and default admin
    with app.app_context():
        db.create_all()
        create_default_admin()

    return app
