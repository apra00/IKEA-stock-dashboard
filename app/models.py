from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from .extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")  # admin/user
    email = db.Column(db.String(255), nullable=True)  # notification email

    items = db.relationship("Item", backref="user", lazy="dynamic")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_edit_items(self) -> bool:
        # Only admins and normal users; no 'viewer' role anymore
        return self.role in ("admin", "user")


class Folder(db.Model):
    __tablename__ = "folders"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref="folders")

    def __repr__(self):
        return f"<Folder {self.name} (user={self.user_id})>"


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)
    added_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    product_id = db.Column(db.String(32), nullable=False)
    country_code = db.Column(db.String(8), nullable=False)
    store_ids = db.Column(db.String(255), nullable=True)  # comma-separated buCodes
    is_active = db.Column(db.Boolean, default=True)

    last_stock = db.Column(db.Integer, nullable=True)
    last_probability = db.Column(db.String(64), nullable=True)
    last_checked = db.Column(db.DateTime, nullable=True)

    # Notification settings
    notify_enabled = db.Column(db.Boolean, default=False)
    notify_threshold = db.Column(db.Integer, nullable=True)
    last_notified_at = db.Column(db.DateTime, nullable=True)

    # Ownership & folder
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    folder_id = db.Column(db.Integer, db.ForeignKey("folders.id"), nullable=True)

    folder = db.relationship("Folder", backref="items")

    history = db.relationship(
        "AvailabilitySnapshot",
        backref="item",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Item {self.name} ({self.product_id}) user={self.user_id}>"


class AvailabilitySnapshot(db.Model):
    __tablename__ = "availability_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    total_stock = db.Column(db.Integer, nullable=True)
    probability_summary = db.Column(db.String(64), nullable=True)
    raw_json = db.Column(db.Text, nullable=True)  # optional: store full JSON


def create_default_admin():
    """
    Create an initial admin user if no users exist.
    """
    if User.query.count() == 0:
        username = os.environ.get("INITIAL_ADMIN_USERNAME", "admin")
        pwd = os.environ.get("INITIAL_ADMIN_PASSWORD")
        if not pwd:
            # 20 random characters if not explicitly set
            pwd = secrets.token_urlsafe(20)

        admin = User(username=username, role="admin")
        admin.set_password(pwd)
        db.session.add(admin)
        db.session.commit()
        print(
            f"Created default admin user: {username} / {pwd} "
            "(please change this immediately)."
        )
