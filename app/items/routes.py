from datetime import datetime, timedelta

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Item, AvailabilitySnapshot, Folder
from ..ikea_service import (
    check_item,
    check_all_active_items,
    get_stores_for_country,
    get_live_availability_for_item,
)

items_bp = Blueprint("items", __name__, url_prefix="/items")


def _require_edit_permission():
    if not current_user.can_edit_items:
        flash("You do not have permission to edit items.", "danger")
        return False
    return True


def _get_or_create_folder_for_user(user_id, folder_name: str):
    folder = Folder.query.filter_by(user_id=user_id, name=folder_name).first()
    if folder:
        return folder
    folder = Folder(user_id=user_id, name=folder_name)
    db.session.add(folder)
    db.session.commit()
    return folder


def _cleanup_empty_folders(user_id):
    """
    Delete folders for the user that have no remaining items.
    """
    empty = (
        Folder.query.filter_by(user_id=user_id)
        .outerjoin(Item)
        .group_by(Folder.id)
        .having(db.func.count(Item.id) == 0)
        .all()
    )
    for f in empty:
        db.session.delete(f)
    if empty:
        db.session.commit()


@items_bp.route("/")
@login_required
def list_items():
    # --- sorting: read from query or session ---
    allowed_sort_columns = {
        "added_at": Item.added_at,
        "name": Item.name,
        "product_id": Item.product_id,
        "country_code": Item.country_code,
        "stores": Item.store_ids,
        "active": Item.is_active,
        "last_stock": Item.last_stock,
        "last_checked": Item.last_checked,
        "last_probability": Item.last_probability,
        "folder": Folder.name,
        "owner": None,  # handled separately
    }

    sort_by = request.args.get("sort") or session.get("items_sort_by") or "added_at"
    sort_dir = request.args.get("dir") or session.get("items_sort_dir") or "desc"

    if sort_by not in allowed_sort_columns:
        sort_by = "added_at"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    session["items_sort_by"] = sort_by
    session["items_sort_dir"] = sort_dir

    query = Item.query

    # non-admin users only see their own items
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    # join folders for folder sorting
    if sort_by == "folder":
        query = query.outerjoin(Folder)

    if sort_by == "owner":
        # only admins can sort by owner
        if current_user.is_admin:
            query = query.outerjoin(Item.user)
            col = db.func.lower(db.func.coalesce(db.literal_column("users.username"), ""))
        else:
            col = Item.added_at
    else:
        col = allowed_sort_columns[sort_by]

    if sort_dir == "asc":
        query = query.order_by(col.asc().nullslast())
    else:
        query = query.order_by(col.desc().nullslast())

    items = query.all()

    # group items by folder/category name
    folders = {}
    for item in items:
        folder_name = item.folder.name if item.folder else "Uncategorized"
        folders.setdefault(folder_name, []).append(item)

    return render_template(
        "items/list.html",
        folders=folders,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@items_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_item():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        product_id = request.form.get("product_id", "").strip()
        country_code = request.form.get("country_code", "").strip().lower()
        store_ids = request.form.get("store_ids", "").strip()
        folder_name = request.form.get("folder_name", "").strip()

        if not name or not product_id or not country_code:
            flash("Name, product ID and country code are required.", "danger")
        else:
            item = Item(
                name=name,
                product_id=product_id,
                country_code=country_code,
                store_ids=store_ids or None,
                user_id=current_user.id,
                is_active=True,
            )

            item.notify_enabled = request.form.get("notify_enabled") == "on"
            notify_threshold_raw = request.form.get("notify_threshold", "").strip()
            if notify_threshold_raw:
                try:
                    item.notify_threshold = int(notify_threshold_raw)
                except ValueError:
                    flash("Notification threshold must be an integer.", "danger")
                    categories = (
                        Folder.query.filter_by(user_id=current_user.id)
                        .order_by(Folder.name.asc())
                        .all()
                    )
                    return render_template(
                        "items/form.html", item=None, categories=categories
                    )

            if folder_name:
                folder = _get_or_create_folder_for_user(current_user.id, folder_name)
                item.folder = folder

            db.session.add(item)
            db.session.commit()
            flash("Item created.", "success")
            return redirect(url_for("items.list_items"))

    categories = (
        Folder.query.filter_by(user_id=current_user.id)
        .order_by(Folder.name.asc())
        .all()
    )
    return render_template("items/form.html", item=None, categories=categories)


@items_bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_item(item_id):
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    item = Item.query.get_or_404(item_id)

    # Non-admin users can edit only their own items
    if not current_user.is_admin and item.user_id != current_user.id:
        flash("You are not allowed to edit this item.", "danger")
        return redirect(url_for("items.list_items"))

    if request.method == "POST":
        item.name = request.form.get("name", "").strip()
        item.product_id = request.form.get("product_id", "").strip()
        item.country_code = request.form.get("country_code", "").strip().lower()
        store_ids = request.form.get("store_ids", "").strip()
        folder_name = request.form.get("folder_name", "").strip()
        item.store_ids = store_ids or None
        item.is_active = request.form.get("is_active") == "on"

        notify_enabled = request.form.get("notify_enabled") == "on"
        notify_threshold_raw = request.form.get("notify_threshold", "").strip()
        notify_threshold = None
        if notify_threshold_raw:
            try:
                notify_threshold = int(notify_threshold_raw)
            except ValueError:
                flash("Notification threshold must be an integer.", "danger")
                categories = (
                    Folder.query.filter_by(user_id=item.user_id)
                    .order_by(Folder.name.asc())
                    .all()
                )
                return render_template(
                    "items/form.html", item=item, categories=categories
                )

        item.notify_enabled = notify_enabled
        item.notify_threshold = notify_threshold

        if folder_name:
            folder = _get_or_create_folder_for_user(item.user_id, folder_name)
            item.folder = folder
        else:
            # Item is being removed from its category
            item.folder = None

        if not item.name or not item.product_id or not item.country_code:
            flash("Name, product ID and country code are required.", "danger")
        else:
            # Clean up any categories that became empty after moving/removing this item
            _cleanup_empty_folders(item.user_id)
            db.session.commit()
            flash("Item updated.", "success")
            return redirect(url_for("items.list_items"))

    categories = (
        Folder.query.filter_by(user_id=item.user_id)
        .order_by(Folder.name.asc())
        .all()
    )
    return render_template("items/form.html", item=item, categories=categories)


@items_bp.route("/bulk", methods=["POST"])
@login_required
def bulk_update():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    action = request.form.get("bulk_action")
    item_ids = request.form.getlist("item_ids")

    if not item_ids:
        flash("Please select at least one item.", "danger")
        return redirect(url_for("items.list_items"))

    items = Item.query.filter(Item.id.in_(item_ids)).all()

    # Non-admin users can only bulk-edit their own items
    if not current_user.is_admin:
        for it in items:
            if it.user_id != current_user.id:
                flash("You are not allowed to bulk edit items you do not own.", "danger")
                return redirect(url_for("items.list_items"))

    if action == "activate":
        for it in items:
            it.is_active = True
        db.session.commit()
        flash("Items activated.", "success")

    elif action == "deactivate":
        for it in items:
            it.is_active = False
        db.session.commit()
        flash("Items deactivated.", "success")

    elif action == "delete":
        user_id = items[0].user_id if items else None
        for it in items:
            db.session.delete(it)
        if user_id:
            _cleanup_empty_folders(user_id)
        db.session.commit()
        flash("Items deleted.", "info")

    elif action == "edit":
        categories = (
            Folder.query.filter_by(user_id=items[0].user_id)
            .order_by(Folder.name.asc())
            .all()
        )
        default_folder_name = items[0].folder.name if items[0].folder else ""
        return render_template(
            "items/bulk_edit.html",
            items=items,
            categories=categories,
            default_folder_name=default_folder_name,
        )

    else:
        flash("Unknown bulk action.", "danger")

    return redirect(url_for("items.list_items"))


@items_bp.route("/bulk-edit", methods=["POST"])
@login_required
def bulk_edit_submit():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    item_ids = request.form.getlist("item_ids")
    items = Item.query.filter(Item.id.in_(item_ids)).all()

    if not items:
        flash("No items selected.", "danger")
        return redirect(url_for("items.list_items"))

    # Non-admin users can only bulk-edit their own items
    if not current_user.is_admin:
        for it in items:
            if it.user_id != current_user.id:
                flash("You are not allowed to bulk edit items you do not own.", "danger")
                return redirect(url_for("items.list_items"))

    folder_name = request.form.get("folder_name", "").strip()
    is_active = request.form.get("is_active") == "on"

    notify_enabled = request.form.get("notify_enabled") == "on"
    notify_threshold_raw = request.form.get("notify_threshold", "").strip()
    notify_threshold = None
    if notify_threshold_raw:
        try:
            notify_threshold = int(notify_threshold_raw)
        except ValueError:
            flash("Notification threshold must be an integer.", "danger")
            categories = (
                Folder.query.filter_by(user_id=items[0].user_id)
                .order_by(Folder.name.asc())
                .all()
            )
            default_folder_name = items[0].folder.name if items[0].folder else ""
            return render_template(
                "items/bulk_edit.html",
                items=items,
                categories=categories,
                default_folder_name=default_folder_name,
            )

    for it in items:
        it.is_active = is_active
        it.notify_enabled = notify_enabled
        it.notify_threshold = notify_threshold

        if folder_name:
            folder = _get_or_create_folder_for_user(it.user_id, folder_name)
            it.folder = folder
        else:
            it.folder = None

    _cleanup_empty_folders(items[0].user_id)
    db.session.commit()
    flash("Bulk changes applied.", "success")
    return redirect(url_for("items.list_items"))


@items_bp.route("/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(item_id):
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    item = Item.query.get_or_404(item_id)

    if not current_user.is_admin and item.user_id != current_user.id:
        flash("You are not allowed to delete this item.", "danger")
        return redirect(url_for("items.list_items"))

    user_id = item.user_id
    db.session.delete(item)
    # Clean up categories for that user which are now empty
    _cleanup_empty_folders(user_id)
    db.session.commit()
    flash("Item deleted.", "info")
    return redirect(url_for("items.list_items"))


@items_bp.route("/<int:item_id>")
@login_required
def detail(item_id):
    item = Item.query.get_or_404(item_id)

    if not current_user.is_admin and item.user_id != current_user.id:
        flash("You are not allowed to view this item.", "danger")
        return redirect(url_for("items.list_items"))

    # -----------------------------
    # History table (only stock changes)
    # -----------------------------
    raw_history = (
        AvailabilitySnapshot.query.filter_by(item_id=item.id)
        .order_by(AvailabilitySnapshot.timestamp.desc())
        .limit(200)
        .all()
    )

    history_changed = []
    prev_stock = object()
    for snap in raw_history:
        cur_stock = snap.total_stock
        if cur_stock != prev_stock:
            history_changed.append(snap)
            prev_stock = cur_stock
        if len(history_changed) >= 30:
            break

    # -----------------------------
    # Chart history (timerange filter)
    # -----------------------------
    range_key = request.args.get("range", "30d").lower()
    now = datetime.utcnow()

    cutoff = None
    if range_key == "24h":
        cutoff = now - timedelta(hours=24)
    elif range_key == "7d":
        cutoff = now - timedelta(days=7)
    elif range_key == "30d":
        cutoff = now - timedelta(days=30)
    elif range_key == "all":
        cutoff = None
    else:
        range_key = "30d"
        cutoff = now - timedelta(days=30)

    chart_query = AvailabilitySnapshot.query.filter_by(item_id=item.id)
    if cutoff is not None:
        chart_query = chart_query.filter(AvailabilitySnapshot.timestamp >= cutoff)

    chart_history = chart_query.order_by(AvailabilitySnapshot.timestamp.asc()).all()

    # Keep chart sane for huge histories
    if len(chart_history) > 500:
        chart_history = chart_history[-500:]

    chart_labels = [
        (h.timestamp or now).strftime("%Y-%m-%d %H:%M")
        for h in chart_history
    ]
    chart_stock = [h.total_stock or 0 for h in chart_history]

    # Live per-store availability for detailed view
    live_data, live_error = get_live_availability_for_item(item)

    return render_template(
        "items/detail.html",
        item=item,
        history=history_changed,
        chart_history=chart_history,
        chart_labels=chart_labels,
        chart_stock=chart_stock,
        live_data=live_data,
        live_error=live_error,
        range_key=range_key,
    )


@items_bp.route("/<int:item_id>/check", methods=["POST"])
@login_required
def check_single(item_id):
    if not _require_edit_permission():
        return redirect(url_for("items.detail", item_id=item_id))

    item = Item.query.get_or_404(item_id)

    if not current_user.is_admin and item.user_id != current_user.id:
        flash("You are not allowed to check this item.", "danger")
        return redirect(url_for("items.list_items"))

    ok, err = check_item(item)
    if ok:
        flash("Availability updated and snapshot saved.", "success")
    else:
        flash(f"Availability check failed: {err}", "danger")
    return redirect(url_for("items.detail", item_id=item_id))


@items_bp.route("/stores")
@login_required
def list_stores():
    country = request.args.get("country", "").strip().lower()
    stores = []
    error = None

    if country:
        stores, error = get_stores_for_country(country)

    return render_template(
        "items/stores.html",
        country=country,
        stores=stores,
        error=error,
    )
