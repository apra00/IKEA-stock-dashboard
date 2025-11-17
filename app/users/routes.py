from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from flask_login import login_required, current_user
from ..extensions import db
from ..models import User

users_bp = Blueprint("users", __name__, url_prefix="/users")


def _require_admin():
    if not current_user.is_admin:
        flash("Admin privileges required.", "danger")
        return False
    return True


@users_bp.route("/")
@login_required
def list_users():
    if not _require_admin():
        return redirect(url_for("dashboard.index"))

    users = User.query.order_by(User.username.asc()).all()
    return render_template("users/list.html", users=users)


@users_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_user():
    if not _require_admin():
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "user")
        email = request.form.get("email", "").strip()

        if not username or not password:
            flash("Username and password are required.", "danger")
        else:
            if User.query.filter_by(username=username).first():
                flash("Username already exists.", "danger")
            else:
                user = User(username=username, role=role)
                user.set_password(password)
                user.email = email or None
                db.session.add(user)
                db.session.commit()
                flash("User created.", "success")
                return redirect(url_for("users.list_users"))

    return render_template("users/form.html", user=None)


@users_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_user(user_id):
    if not _require_admin():
        return redirect(url_for("dashboard.index"))

    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        role = request.form.get("role", "user")
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip()

        user.role = role
        user.email = email or None
        if password:
            user.set_password(password)

        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/form.html", user=user)


@users_bp.route("/<int:user_id>/delete", methods=["POST"])
@login_required
def delete_user(user_id):
    if not _require_admin():
        return redirect(url_for("dashboard.index"))

    user = User.query.get_or_404(user_id)
    if user.username == "admin":
        flash("Cannot delete the default admin user.", "danger")
        return redirect(url_for("users.list_users"))

    db.session.delete(user)
    db.session.commit()
    flash("User deleted.", "info")
    return redirect(url_for("users.list_users"))
