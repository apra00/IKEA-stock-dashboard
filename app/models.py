from datetime import datetime
import os
import secrets

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

    # New: tags owned by this user
    tags = db.relationship(
        "Tag",
        backref="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

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


# Association table for many-to-many Item <-> Tag
item_tags = db.Table(
    "item_tags",
    db.Column("item_id", db.Integer, db.ForeignKey("items.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tags.id"), primary_key=True),
)


class Tag(db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(64), nullable=False)

    user = db.relationship("User", backref="tags")
    items = db.relationship(
        "Item", secondary="item_tags", back_populates="tags"
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_user_tag_name"),
    )

    def __repr__(self):
        return f"<Tag {self.name} user={self.user_id}>"


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    name = db.Column(db.String(255), nullable=False)
    product_id = db.Column(db.String(64), nullable=False)
    country_code = db.Column(db.String(8), nullable=False)

    # Optional specific store IDs (CSV) or None -> all stores
    store_ids = db.Column(db.String(255), nullable=True)

    # Item active / paused
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Last availability info
    last_stock = db.Column(db.Integer, nullable=True)
    last_probability = db.Column(db.String(255), nullable=True)
    last_checked = db.Column(db.DateTime, nullable=True)

    # Threshold notification config
    notify_threshold = db.Column(db.Integer, nullable=True)  # total stock threshold
    notify_enabled = db.Column(db.Boolean, default=False, nullable=False)
    last_notified_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    folder_id = db.Column(db.Integer, db.ForeignKey("folders.id"), nullable=True)
    folder = db.relationship("Folder", backref="items")

    tags = db.relationship(
    "Tag",
    secondary="item_tags",
    back_populates="items",
    lazy="joined",
)

    def __repr__(self):
        return (
            f"<Item {self.name} (product_id={self.product_id}, "
            f"user_id={self.user_id})>"
        )


class AvailabilitySnapshot(db.Model):
    __tablename__ = "availability_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    total_stock = db.Column(db.Integer, nullable=True)
    probability_summary = db.Column(db.String(255), nullable=True)
    raw_json = db.Column(db.Text, nullable=True)

    item = db.relationship("Item", backref="availability_snapshots")


def create_default_admin():
    """
    Create a default admin user if no users exist.

    Security: generates a random password, prints it to console.
    This is intended for first-time setup only.
    """
    if User.query.count() > 0:
        return

    username = "admin"
    random_password = secrets.token_urlsafe(12)
    admin = User(username=username, role="admin")
    admin.set_password(random_password)
    db.session.add(admin)
    db.session.commit()

    print("=" * 60)
    print("Default admin user created:")
    print(f"  Username: {username}")
    print(f"  Password: {random_password}")
    print("=" * 60)
    print(
        "Please log in immediately and change this password, "
        "then create additional users as needed."
    )
