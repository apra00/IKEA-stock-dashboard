from datetime import datetime

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
    get_stores_for_country,
    get_live_availability_for_item,
)

items_bp = Blueprint("items", __name__, url_prefix="/items")


def _require_edit_permission():
    if not current_user.can_edit_items:
        flash("You are not authorized to modify items.", "danger")
        return False
    return True


def _get_or_create_folder_for_user(user_id: int, name: str | None):
    if not name:
        return None
    clean_name = name.strip()
    if not clean_name:
        return None

    folder = Folder.query.filter_by(user_id=user_id, name=clean_name).first()
    if folder:
        return folder

    folder = Folder(user_id=user_id, name=clean_name)
    db.session.add(folder)
    db.session.flush()  # ensure id is available before commit
    return folder


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

    sort_by = request.args.get("sort")
    sort_dir = request.args.get("dir")

    # If not provided in query, fall back to session values
    if not sort_by:
        sort_by = session.get("items_sort_by", "name")
    if not sort_dir:
        sort_dir = session.get("items_sort_dir", "asc")

    # Validate sort_by
    if sort_by not in allowed_sort_columns:
        sort_by = "name"

    # Validate sort_dir
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"

    # Remember in session
    session["items_sort_by"] = sort_by
    session["items_sort_dir"] = sort_dir

    # Base query: per-user separation
    if current_user.is_admin:
        query = Item.query.outerjoin(Folder)
    else:
        query = Item.query.filter_by(user_id=current_user.id).outerjoin(Folder)

    # Sorting
    if sort_by == "folder":
        sort_column = Folder.name
    elif sort_by == "owner":
        if current_user.is_admin:
            from ..models import User  # local import to avoid circular

            query = query.join(User)
            sort_column = User.username
        else:
            sort_column = Item.name
    else:
        sort_column = allowed_sort_columns[sort_by]

    if sort_dir == "desc":
        sort_column = sort_column.desc()

    items = query.order_by(sort_column).all()

    # Group items by folder/category (for collapsible groups in the view)
    folder_groups: dict[str, list[Item]] = {}
    for item in items:
        group_name = item.folder.name if item.folder else "No category"
        if group_name not in folder_groups:
            folder_groups[group_name] = []
        folder_groups[group_name].append(item)

    return render_template(
        "items/list.html",
        folders=folder_groups,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@items_bp.route("/bulk", methods=["POST"])
@login_required
def bulk_update():
    """
    Bulk actions on items from the list view:
    - activate
    - deactivate
    - delete
    - edit (redirects to bulk edit form)
    """
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    raw_ids = request.form.getlist("item_ids")
    action = request.form.get("bulk_action", "").strip()

    # Parse ids safely
    try:
        item_ids = [int(x) for x in raw_ids]
    except ValueError:
        item_ids = []

    if not item_ids:
        flash("No items selected for bulk action.", "warning")
        return redirect(url_for("items.list_items"))

    if action not in {"activate", "deactivate", "delete", "edit"}:
        flash("Unknown bulk action.", "danger")
        return redirect(url_for("items.list_items"))

    # Base query; non-admins can only touch their own items
    query = Item.query.filter(Item.id.in_(item_ids))
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    items = query.all()
    if not items:
        flash("You are not allowed to modify the selected items.", "danger")
        return redirect(url_for("items.list_items"))

    # Bulk EDIT: redirect to dedicated form
    if action == "edit":
        ids_str = ",".join(str(i.id) for i in items)
        return redirect(url_for("items.bulk_edit", ids=ids_str))

    # Other bulk actions operate in-place
    updated = 0
    deleted = 0

    if action == "activate":
        for item in items:
            if not item.is_active:
                item.is_active = True
                updated += 1
    elif action == "deactivate":
        for item in items:
            if item.is_active:
                item.is_active = False
                updated += 1
    elif action == "delete":
        for item in items:
            db.session.delete(item)
            deleted += 1

    db.session.commit()

    if action == "delete":
        flash(f"Deleted {deleted} items.", "success")
    else:
        flash(f"Updated {updated} items.", "success")

    return redirect(url_for("items.list_items"))


@items_bp.route("/bulk-edit", methods=["GET", "POST"])
@login_required
def bulk_edit():
    """
    Bulk edit form:
    - same feel as edit form
    - unique fields disabled
    - editable: category, active, notifications
    """
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    if request.method == "GET":
        ids_param = request.args.get("ids", "")
        raw_ids = [x for x in ids_param.split(",") if x.strip()]
        try:
            item_ids = [int(x) for x in raw_ids]
        except ValueError:
            item_ids = []

        if not item_ids:
            flash("No items selected.", "warning")
            return redirect(url_for("items.list_items"))

        query = Item.query.filter(Item.id.in_(item_ids))
        if not current_user.is_admin:
            query = query.filter_by(user_id=current_user.id)

        items = query.all()
        if not items:
            flash("You are not allowed to edit the selected items.", "danger")
            return redirect(url_for("items.list_items"))

        # Categories for this user (or for admin: use owner of first item)
        owner_id = items[0].user_id
        categories = (
            Folder.query.filter_by(user_id=owner_id).order_by(Folder.name.asc()).all()
        )

        # Pre-select category only if all share the same one
        first_folder = items[0].folder
        same_folder = all(i.folder and first_folder and i.folder.id == first_folder.id for i in items)
        default_folder_name = first_folder.name if same_folder and first_folder else ""

        return render_template(
            "items/bulk_edit.html",
            items=items,
            categories=categories,
            default_folder_name=default_folder_name,
        )

    # POST (update all selected items)
    raw_ids = request.form.getlist("item_ids")
    try:
        item_ids = [int(x) for x in raw_ids]
    except ValueError:
        item_ids = []

    if not item_ids:
        flash("No items selected.", "warning")
        return redirect(url_for("items.list_items"))

    query = Item.query.filter(Item.id.in_(item_ids))
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    items = query.all()
    if not items:
        flash("You are not allowed to edit the selected items.", "danger")
        return redirect(url_for("items.list_items"))

    folder_name = request.form.get("folder_name", "").strip()
    is_active = request.form.get("is_active") == "on"
    has_is_active = "is_active" in request.form  # always there; simple behaviour

    notify_enabled = request.form.get("notify_enabled") == "on"
    has_notify_enabled = "notify_enabled" in request.form

    notify_threshold_raw = request.form.get("notify_threshold", "").strip()
    notify_threshold = None
    if notify_threshold_raw:
        try:
            notify_threshold = int(notify_threshold_raw)
        except ValueError:
            flash("Notification threshold must be an integer.", "danger")
            # Reload with same items
            owner_id = items[0].user_id
            categories = (
                Folder.query.filter_by(user_id=owner_id)
                .order_by(Folder.name.asc())
                .all()
            )
            return render_template(
                "items/bulk_edit.html",
                items=items,
                categories=categories,
                default_folder_name=folder_name,
            )

    # Apply changes to each item
    for item in items:
        # Category
        if folder_name:
            folder = _get_or_create_folder_for_user(item.user_id, folder_name)
            item.folder = folder
        else:
            item.folder = None

        # Active flag (simple: apply same to all)
        item.is_active = is_active if has_is_active else item.is_active

        # Notifications
        item.notify_enabled = notify_enabled if has_notify_enabled else item.notify_enabled
        item.notify_threshold = notify_threshold

    db.session.commit()
    flash(f"Bulk-edited {len(items)} items.", "success")
    return redirect(url_for("items.list_items"))


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
                    Folder.query.filter_by(user_id=current_user.id)
                    .order_by(Folder.name.asc())
                    .all()
                )
                return render_template(
                    "items/form.html",
                    item=None,
                    default_country_code=country_code,
                    default_store_ids=store_ids,
                    default_folder_name=folder_name,
                    categories=categories,
                )

        if not name or not product_id or not country_code:
            flash("Name, product ID and country code are required.", "danger")
        else:
            folder = _get_or_create_folder_for_user(current_user.id, folder_name)
            item = Item(
                name=name,
                product_id=product_id,
                country_code=country_code,
                store_ids=store_ids or None,
                is_active=is_active,
                notify_enabled=notify_enabled,
                notify_threshold=notify_threshold,
                user_id=current_user.id,
                folder=folder,
            )
            db.session.add(item)
            db.session.commit()
            flash("Item created.", "success")
            return redirect(url_for("items.list_items"))

    # GET: prefill with last item's country/store/category to "remember" settings
    last_item = (
        Item.query.filter_by(user_id=current_user.id)
        .order_by(Item.id.desc())
        .first()
    )
    default_country_code = last_item.country_code if last_item else ""
    default_store_ids = last_item.store_ids if last_item and last_item.store_ids else ""
    default_folder_name = (
        last_item.folder.name if last_item and last_item.folder else ""
    )

    categories = (
        Folder.query.filter_by(user_id=current_user.id)
        .order_by(Folder.name.asc())
        .all()
    )

    return render_template(
        "items/form.html",
        item=None,
        default_country_code=default_country_code,
        default_store_ids=default_store_ids,
        default_folder_name=default_folder_name,
        categories=categories,
    )


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
            item.folder = None

        if not item.name or not item.product_id or not item.country_code:
            flash("Name, product ID and country code are required.", "danger")
        else:
            db.session.commit()
            flash("Item updated.", "success")
            return redirect(url_for("items.list_items"))

    categories = (
        Folder.query.filter_by(user_id=item.user_id)
        .order_by(Folder.name.asc())
        .all()
    )
    return render_template("items/form.html", item=item, categories=categories)


@items_bp.route("/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(item_id):
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    item = Item.query.get_or_404(item_id)

    if not current_user.is_admin and item.user_id != current_user.id:
        flash("You are not allowed to delete this item.", "danger")
        return redirect(url_for("items.list_items"))

    db.session.delete(item)
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

    # last 30 snapshots (table)
    history = (
        AvailabilitySnapshot.query.filter_by(item_id=item.id)
        .order_by(AvailabilitySnapshot.timestamp.desc())
        .limit(30)
        .all()
    )

    # last 30 snapshots for chart in ascending order
    total_count = AvailabilitySnapshot.query.filter_by(item_id=item.id).count()
    offset = max(0, total_count - 30)
    chart_history = (
        AvailabilitySnapshot.query.filter_by(item_id=item.id)
        .order_by(AvailabilitySnapshot.timestamp.asc())
        .offset(offset)
        .limit(30)
        .all()
    )

    # Prepare data for chart.js (format timestamps as date + HH:MM)
    chart_labels = [
        (h.timestamp or datetime.utcnow()).strftime("%Y-%m-%d %H:%M")
        for h in chart_history
    ]
    chart_stock = [h.total_stock or 0 for h in chart_history]

    # Live per-store availability for detailed view
    live_data, live_error = get_live_availability_for_item(item)

    return render_template(
        "items/detail.html",
        item=item,
        history=history,
        chart_history=chart_history,
        chart_labels=chart_labels,
        chart_stock=chart_stock,
        live_data=live_data,
        live_error=live_error,
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
