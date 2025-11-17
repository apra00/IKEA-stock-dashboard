from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func
from ..extensions import db
from ..models import Item, AvailabilitySnapshot
from ..ikea_service import check_all_active_items

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    total_items = Item.query.count()
    available_items = (
        Item.query.filter(Item.last_stock.isnot(None), Item.last_stock > 0).count()
    )
    last_check = db.session.query(func.max(Item.last_checked)).scalar()

    latest_snapshots = (
        db.session.query(AvailabilitySnapshot)
        .order_by(AvailabilitySnapshot.timestamp.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "dashboard/index.html",
        total_items=total_items,
        available_items=available_items,
        last_check=last_check,
        latest_snapshots=latest_snapshots,
    )


@dashboard_bp.route("/check-all", methods=["POST"])
@login_required
def check_all():
    if not current_user.can_edit_items:
        flash("You are not authorized to perform this action.", "danger")
        return redirect(url_for("dashboard.index"))

    ok, failed = check_all_active_items()
    flash(f"Availability check finished. OK: {ok}, failed: {failed}", "info")
    return redirect(url_for("dashboard.index"))
