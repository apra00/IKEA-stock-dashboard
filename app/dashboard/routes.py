from datetime import datetime, timedelta
from threading import Lock, Thread

from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request,
    jsonify,
    current_app,
)
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from collections import Counter


from ..extensions import db
from ..models import Item, AvailabilitySnapshot
from ..ikea_service import check_all_active_items




dashboard_bp = Blueprint("dashboard", __name__)

# -------------------------------------------------------------------
# In-memory "check running" tracking (best effort)
# -------------------------------------------------------------------
_CHECK_RUNNING: dict[int, datetime] = {}
_CHECK_LOCK = Lock()


def _set_running(user_id: int, running: bool) -> None:
    with _CHECK_LOCK:
        if running:
            _CHECK_RUNNING[user_id] = datetime.utcnow()
        else:
            _CHECK_RUNNING.pop(user_id, None)


def _is_running(user_id: int) -> bool:
    with _CHECK_LOCK:
        return user_id in _CHECK_RUNNING


def _humanize_ago(dt: datetime | None) -> str | None:
    """Return a short 'how long ago' string like '8m ago', '2h ago', '3d ago'."""
    if not dt:
        return None

    now = datetime.utcnow()
    try:
        delta = now - dt.replace(tzinfo=None)
    except Exception:
        delta = now - dt

    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = 0

    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 4:
        return f"{weeks}w ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _run_check_all_in_background(app, user_id: int) -> None:
    """
    Background runner for check_all.
    The Flask app is passed in so we can create an app context in this thread.
    """
    with app.app_context():
        try:
            from ..models import User  # local import to avoid circulars

            user = User.query.get(user_id)
            if not user:
                return

            # Admins: check all active items; normal users: just their own
            if user.is_admin:
                check_all_active_items(None)
            else:
                check_all_active_items(user.id)
        finally:
            _set_running(user_id, False)


# -------------------------------------------------------------------
# Dashboard
# -------------------------------------------------------------------
@dashboard_bp.route("/")
@login_required
def index():
    # Base query for visible items
    if current_user.is_admin:
        item_query = Item.query
    else:
        item_query = Item.query.filter_by(user_id=current_user.id)

    # --- Counters ---------------------------------------------------
    total_items = item_query.count()
    active_items = item_query.filter_by(is_active=True).count()
    inactive_items = total_items - active_items

    in_stock_items = (
        item_query.filter(Item.last_stock.isnot(None), Item.last_stock > 0).count()
    )
    out_of_stock_items = item_query.filter(Item.last_stock == 0).count()
    unknown_stock_items = total_items - in_stock_items - out_of_stock_items

    notify_enabled_items = item_query.filter_by(notify_enabled=True).count()

    # --- Last check + latest activity -------------------------------
    snap_query = AvailabilitySnapshot.query.join(Item)
    if not current_user.is_admin:
        snap_query = snap_query.filter(Item.user_id == current_user.id)

    last_snapshot = snap_query.order_by(
        AvailabilitySnapshot.timestamp.desc()
    ).first()
    last_check = last_snapshot.timestamp if last_snapshot else None
    last_check_ago = _humanize_ago(last_check)

    latest_snapshots = (
        snap_query.order_by(AvailabilitySnapshot.timestamp.desc())
        .limit(20)
        .all()
    )

    # --- Recently checked items ------------------------------------
    recently_checked_items = (
        item_query.filter(Item.last_checked.isnot(None))
        .order_by(Item.last_checked.desc().nullslast())
        .limit(8)
        .all()
    )

    # --- Recently created items (USES created_at) ---------------------
    recently_created_items = (
        item_query.order_by(Item.created_at.desc().nullslast())
        .limit(8)
        .all()
    )

    # --- Items that changed stock in last 24h -----------------------
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    changed_item_ids_rows = (
        db.session.query(AvailabilitySnapshot.item_id)
        .filter(AvailabilitySnapshot.timestamp >= cutoff_24h)
        .group_by(AvailabilitySnapshot.item_id)
        .having(func.count(AvailabilitySnapshot.id) >= 2)
        .all()
    )
    changed_item_ids = [row[0] for row in changed_item_ids_rows]

    changed_recently_items = []
    if changed_item_ids:
        changed_recently_items = (
            item_query.filter(Item.id.in_(changed_item_ids))
            .order_by(Item.last_checked.desc().nullslast())
            .limit(8)
            .all()
        )

    # --- NEW: top tags across visible items -------------------------
    top_tags: list[dict[str, int]] = []
    # Only load items that actually have tags, with tags pre-loaded
    tagged_items = (
        item_query.options(selectinload(Item.tags))
        .filter(Item.tags.any())
        .all()
    )

    tag_counter: Counter[str] = Counter()
    for it in tagged_items:
        for t in (it.tags or []):
            if t and t.name:
                tag_counter[t.name] += 1

    top_tags = [
        {"name": name, "count": count}
        for name, count in sorted(
            tag_counter.items(), key=lambda kv: (-kv[1], kv[0].lower())
        )[:10]  # top 10 tags
    ]

    check_running = _is_running(current_user.id)

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
        last_check_ago=last_check_ago,
        latest_snapshots=latest_snapshots,
        recently_checked_items=recently_checked_items,
        recently_created_items=recently_created_items,
        changed_recently_items=changed_recently_items,
        check_running=check_running,
        top_tags=top_tags,   # NEW
    )


@dashboard_bp.route("/check-all/status", methods=["GET"])
@login_required
def check_all_status():
    # lightweight polling endpoint
    return jsonify({"running": _is_running(current_user.id)}), 200


@dashboard_bp.route("/check-all", methods=["POST"])
@login_required
def check_all():
    if not current_user.can_edit_items:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "error", "error": "Unauthorized"}), 403
        flash("You are not authorized to perform this action.", "danger")
        return redirect(url_for("dashboard.index"))

    # If already running, don't start another one
    if _is_running(current_user.id):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"status": "ok", "running": True}), 202
        flash("A refresh is already running.", "info")
        return redirect(url_for("dashboard.index"))

    # Mark running and start a background thread
    _set_running(current_user.id, True)
    app = current_app._get_current_object()
    t = Thread(
        target=_run_check_all_in_background,
        args=(app, current_user.id),
        daemon=True,
    )
    t.start()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        # Frontend will keep spinner + poll /check-all/status
        return jsonify({"status": "ok", "running": True}), 202

    flash("Availability refresh started in background.", "info")
    return redirect(url_for("dashboard.index"))
