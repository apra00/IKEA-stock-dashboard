from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from ..extensions import db
from ..models import Item, AvailabilitySnapshot
from ..ikea_service import check_all_active_items

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    # Per-user separation on dashboard
    item_query = Item.query
    if not current_user.is_admin:
        item_query = item_query.filter_by(user_id=current_user.id)

    total_items = item_query.count()

    active_items = item_query.filter_by(is_active=True).count()
    inactive_items = total_items - active_items

    in_stock_items = item_query.filter(
        Item.last_stock.isnot(None), Item.last_stock > 0
    ).count()

    out_of_stock_items = item_query.filter(
        Item.last_stock.isnot(None), Item.last_stock <= 0
    ).count()

    unknown_stock_items = item_query.filter(Item.last_stock.is_(None)).count()

    notify_enabled_items = item_query.filter(Item.notify_enabled.is_(True)).count()

    last_check = item_query.with_entities(func.max(Item.last_checked)).scalar()

    # Latest activity snapshots
    snapshots_query = (
        db.session.query(AvailabilitySnapshot)
        .join(Item, AvailabilitySnapshot.item_id == Item.id)
    )
    if not current_user.is_admin:
        snapshots_query = snapshots_query.filter(Item.user_id == current_user.id)

    latest_snapshots = snapshots_query.order_by(
        AvailabilitySnapshot.timestamp.desc()
    ).limit(12).all()

    # Recently checked items (for quick glance list)
    recently_checked_items = (
        item_query.order_by(Item.last_checked.desc().nullslast())
        .limit(6)
        .all()
    )

    # Recently added items
    recently_added_items = (
        item_query.order_by(Item.added_at.desc().nullslast())
        .limit(6)
        .all()
    )

    # Items that changed stock in last 24h (simple heuristic)
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    changed_item_ids = (
        db.session.query(AvailabilitySnapshot.item_id)
        .filter(AvailabilitySnapshot.timestamp >= cutoff_24h)
        .group_by(AvailabilitySnapshot.item_id)
        .having(func.count(AvailabilitySnapshot.id) >= 2)
        .all()
    )
    changed_item_ids = [row[0] for row in changed_item_ids]

    changed_recently_items = []
    if changed_item_ids:
        changed_recently_items = (
            item_query.filter(Item.id.in_(changed_item_ids))
            .order_by(Item.last_checked.desc().nullslast())
            .limit(8)
            .all()
        )

    return render_template(
        "dashboard/index.html",
        total_items=total_items,
        active_items=active_items,
        inactive_items=inactive_items,
        in_stock_items=in_stock_items,
        out_of_stock_items=out_of_stock_items,
        unknown_stock_items=unknown_stock_items,
        notify_enabled_items=notify_enabled_items,
        last_check=last_check,
        latest_snapshots=latest_snapshots,
        recently_checked_items=recently_checked_items,
        recently_added_items=recently_added_items,
        changed_recently_items=changed_recently_items,
    )


@dashboard_bp.route("/check-all", methods=["POST"])
@login_required
def check_all():
    if not current_user.can_edit_items:
        flash("You are not authorized to perform this action.", "danger")
        return redirect(url_for("dashboard.index"))

    ok, failed = check_all_active_items(current_user)
    flash(f"Availability check finished. OK: {ok}, failed: {failed}", "info")
    return redirect(url_for("dashboard.index"))
