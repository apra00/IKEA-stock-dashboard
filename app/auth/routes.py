from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user
from ..models import User
from ..extensions import limiter

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@limiter.limit("5 per minute; 20 per hour")
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash("Invalid username or password.", "danger")
        else:
            login_user(user)
            flash("Welcome back!", "success")
            next_url = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_url)

    return render_template("auth/login.html")


@auth_bp.route("/logout")
def logout():
    if current_user.is_authenticated:
        logout_user()
        flash("Logged out.", "info")
    return redirect(url_for("auth.login"))
