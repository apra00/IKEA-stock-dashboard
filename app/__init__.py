from flask import Flask
from .extensions import db, login_manager
from .models import User
from .models import create_default_admin
from .dashboard.routes import dashboard_bp
from .auth.routes import auth_bp
from .items.routes import items_bp
from .users.routes import users_bp
from .api.routes import api_bp
from config import config
from flask_migrate import Migrate
from .extensions import db

migrate = Migrate()


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Init extensions
    db.init_app(app)
    migrate.init_app(app, db)
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
