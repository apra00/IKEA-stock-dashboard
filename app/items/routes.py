from __future__ import annotations

from datetime import datetime, timedelta
import io
import csv
import json
from typing import Any, Dict, List, Tuple

import pandas as pd
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
    current_app,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from ..extensions import db, csrf
from ..models import Item, AvailabilitySnapshot, Folder, Tag
from ..ikea_service import (
    check_item,
    check_all_active_items,
    get_stores_for_country,
    get_live_availability_for_item,
)

items_bp = Blueprint("items", __name__, url_prefix="/items")


def _require_edit_permission(item: Item):
    if not current_user.is_authenticated:
        return False
    if current_user.is_admin:
        return True
    return item.user_id == current_user.id


def _get_or_create_folder_for_user(user_id: int, name: str | None):
    """
    Helper for folder handling.

    - If name is None:
        Return list of folders for the current user (or all if admin).
        This is used to populate dropdowns (import/bulk forms).
    - If name is an empty string:
        Treat as "no folder" and return None.
    - If name is a non-empty string:
        Get (or create) a folder with that name for the given user.
    """
    # Folder list mode (for UI dropdowns)
    if name is None:
        if current_user.is_admin:
            all_folders = Folder.query.order_by(Folder.name.asc()).all()
            seen = set()
            unique_folders: List[Folder] = []
            for f in all_folders:
                if f.name not in seen:
                    seen.add(f.name)
                    unique_folders.append(f)
            return unique_folders
        else:
            return (
                Folder.query.filter_by(user_id=user_id)
                .order_by(Folder.name.asc())
                .all()
            )

    clean_name = name.strip()
    if not clean_name:
        # Explicitly no folder
        return None

    folder = Folder.query.filter_by(user_id=user_id, name=clean_name).first()
    if folder:
        return folder

    folder = Folder(user_id=user_id, name=clean_name)
    db.session.add(folder)
    db.session.flush()
    return folder


def _cleanup_empty_folders(user_id: int):
    """
    Remove folders that no longer contain any items for this user.
    Admins may have global folders; we only clean the current user's own folders.
    """
    empty_folders = (
        Folder.query.filter_by(user_id=user_id)
        .outerjoin(Item)
        .filter(Item.id.is_(None))
        .all()
    )
    for f in empty_folders:
        db.session.delete(f)
    db.session.commit()


# --- Tag helpers -----------------------------------------------------------


def _parse_tag_names(raw: str) -> List[str]:
    """
    Parse a comma-separated string into a list of unique tag names,
    preserving order, ignoring empty pieces, and treating names
    case-insensitively for de-duplication.
    """
    if not raw:
        return []

    # Allow both comma and semicolon separators
    normalised = raw.replace(";", ",")
    parts = [p.strip() for p in normalised.split(",")]
    parts = [p for p in parts if p]

    seen_lower = set()
    result: List[str] = []
    for name in parts:
        key = name.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        result.append(name)
    return result


def _get_or_create_tags_for_user(user_id: int, names: List[str]) -> List[Tag]:
    """
    Given a list of tag names, return Tag objects for this user.
    Creates missing tags as needed.
    """
    if not names:
        return []

    # Load all user's tags once and match in Python;
    # tag count per user is expected to be limited.
    existing_tags = Tag.query.filter_by(user_id=user_id).all()
    by_lower: Dict[str, Tag] = {t.name.lower(): t for t in existing_tags}

    result: List[Tag] = []
    for name in names:
        key = name.lower()
        tag = by_lower.get(key)
        if not tag:
            tag = Tag(user_id=user_id, name=name)
            db.session.add(tag)
            by_lower[key] = tag
        result.append(tag)

    return result


# --- Import helpers (pandas / CSV / XLSX) ----------------------------------


def _ensure_pandas():
    """
    Import pandas lazily. In this project it's already imported at module level,
    but we keep this helper for clarity / future changes.
    """
    if pd is None:  # pragma: no cover - defensive programming
        raise RuntimeError("pandas is required for import functionality")


def _parse_uploaded_table(file_storage) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parse an uploaded CSV/Excel file into a list of dictionaries + column names.

    Returns:
      (rows, columns)
    """
    _ensure_pandas()

    filename = file_storage.filename or ""
    filename = filename.lower()

    if filename.endswith(".csv"):
        df = pd.read_csv(file_storage)
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        df = pd.read_excel(file_storage)
    else:
        # try csv as fallback
        df = pd.read_csv(file_storage)

    df = df.fillna("")
    columns = list(df.columns)
    rows = df.to_dict(orient="records")
    return rows, columns


def _cast_bool(val: str | None) -> bool | None:
    if val is None:
        return None
    v = val.strip().lower()
    if not v:
        return None
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return None


def _cast_int(val: str | None) -> int | None:
    if val is None:
        return None
    v = val.strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


# --- Routes ----------------------------------------------------------------


@items_bp.route("/", methods=["GET"])
@login_required
def list_items():
    """
    List items for the current user. Admins see all items but can filter.
    Provides search, sorting, and folder grouping.
    """
    query = Item.query

    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)
    else:
        user_id = request.args.get("user_id")
        if user_id:
            try:
                user_id_int = int(user_id)
                query = query.filter_by(user_id=user_id_int)
            except ValueError:
                pass

    search = request.args.get("search", "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(Item.name.ilike(like), Item.product_id.ilike(like))
        )

    status_filter = request.args.get("status", "active")
    if status_filter == "active":
        query = query.filter_by(is_active=True)
    elif status_filter == "inactive":
        query = query.filter_by(is_active=False)

    sort_by = request.args.get("sort", "name")
    sort_desc = request.args.get("desc", "0") == "1"

    sort_column = Item.name
    if sort_by == "created":
        sort_column = Item.created_at
    elif sort_by == "last_checked":
        sort_column = Item.last_checked
    elif sort_by == "stock":
        sort_column = Item.last_stock

    if sort_desc:
        sort_column = sort_column.desc()

    # We still group by folder in Python; tags are loaded via selectin.
    items = query.order_by(sort_column).all()

    # Group by folder name (None => "Uncategorized")
    folder_groups: Dict[str, List[Item]] = {}
    for item in items:
        folder_name = item.folder.name if item.folder else "Uncategorized"
        folder_groups.setdefault(folder_name, []).append(item)

    # Keep folder groups sorted alphabetically, with Uncategorized last
    sorted_folder_groups = []
    for name in sorted(folder_groups.keys(), key=lambda n: (n == "Uncategorized", n)):
        sorted_folder_groups.append((name, folder_groups[name]))

    return render_template(
        "items/list.html",
        folder_groups=sorted_folder_groups,
        search=search,
        sort_by=sort_by,
        sort_desc=sort_desc,
        status_filter=status_filter,
    )


@items_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_item():
    """
    Create a new item for the current user.
    """
    if not current_user.can_edit_items:
        flash("You are not allowed to add items.", "danger")
        return redirect(url_for("items.list_items"))

    categories = _get_or_create_folder_for_user(current_user.id, None)
    default_country = (current_user.username or "").split("-")[0].lower() or ""

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        product_id = request.form.get("product_id", "").strip()
        country_code = request.form.get("country_code", "").strip().lower()
        store_ids = request.form.get("store_ids", "").strip()
        is_active = bool(request.form.get("is_active"))

        folder_name = request.form.get("folder_name_hidden", "").strip()

        notify_threshold_raw = request.form.get("notify_threshold", "").strip()
        notify_enabled = bool(request.form.get("notify_enabled"))

        # Tags: free-form comma-separated
        tags_raw = request.form.get("tags", "") or ""
        tag_names = _parse_tag_names(tags_raw)

        if not name or not product_id or not country_code:
            flash("Name, product ID and country code are required.", "danger")
            return render_template(
                "items/form.html",
                item=None,
                categories=categories,
                default_country_code=default_country,
            )

        notify_threshold = None
        if notify_threshold_raw:
            try:
                notify_threshold = int(notify_threshold_raw)
            except ValueError:
                flash("Notification threshold must be an integer.", "danger")
                return render_template(
                    "items/form.html",
                    item=None,
                    categories=categories,
                    default_country_code=default_country,
                )

        folder = _get_or_create_folder_for_user(current_user.id, folder_name)

        item = Item(
            user_id=current_user.id,
            name=name,
            product_id=product_id.replace(".", "").replace(" ", ""),
            country_code=country_code,
            store_ids=store_ids or None,
            is_active=is_active,
            folder=folder,
            notify_threshold=notify_threshold,
            notify_enabled=notify_enabled,
        )

        # Attach tags, if any
        if tag_names:
            item.tags = _get_or_create_tags_for_user(current_user.id, tag_names)

        db.session.add(item)
        db.session.commit()

        flash("Item created.", "success")
        return redirect(url_for("items.list_items"))

    return render_template(
        "items/form.html",
        item=None,
        categories=categories,
        default_country_code=default_country,
    )


@items_bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_item(item_id: int):
    item = Item.query.get_or_404(item_id)

    if not _require_edit_permission(item):
        flash("You are not allowed to edit this item.", "danger")
        return redirect(url_for("items.list_items"))

    categories = _get_or_create_folder_for_user(item.user_id, None)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        product_id = request.form.get("product_id", "").strip()
        country_code = request.form.get("country_code", "").strip().lower()
        store_ids = request.form.get("store_ids", "").strip()
        is_active = bool(request.form.get("is_active"))

        folder_name = request.form.get("folder_name_hidden", "").strip()

        notify_threshold_raw = request.form.get("notify_threshold", "").strip()
        notify_enabled = bool(request.form.get("notify_enabled"))

        tags_raw = request.form.get("tags", "") or ""

        if not name or not product_id or not country_code:
            flash("Name, product ID and country code are required.", "danger")
            return render_template(
                "items/form.html",
                item=item,
                categories=categories,
                default_country_code=item.country_code,
            )

        notify_threshold = None
        if notify_threshold_raw:
            try:
                notify_threshold = int(notify_threshold_raw)
            except ValueError:
                flash("Notification threshold must be an integer.", "danger")
                return render_template(
                    "items/form.html",
                    item=item,
                    categories=categories,
                    default_country_code=item.country_code,
                )

        item.name = name
        item.product_id = product_id.replace(".", "").replace(" ", "")
        item.country_code = country_code
        item.store_ids = store_ids or None
        item.is_active = is_active

        item.notify_threshold = notify_threshold
        item.notify_enabled = notify_enabled

        # Folder
        folder = _get_or_create_folder_for_user(item.user_id, folder_name)
        item.folder = folder

        # Tags: overwrite with the new set
        tag_names = _parse_tag_names(tags_raw)
        if tag_names:
            item.tags = _get_or_create_tags_for_user(item.user_id, tag_names)
        else:
            item.tags = []

        db.session.commit()
        flash("Item updated.", "success")
        return redirect(url_for("items.list_items"))

    return render_template(
        "items/form.html",
        item=item,
        categories=categories,
        default_country_code=item.country_code,
    )


@items_bp.route("/<int:item_id>/delete", methods=["POST"])
@login_required
@csrf.exempt  # CSRF handled via hidden token already
def delete_item(item_id: int):
    item = Item.query.get_or_404(item_id)

    if not _require_edit_permission(item):
        flash("You are not allowed to delete this item.", "danger")
        return redirect(url_for("items.list_items"))

    user_id = item.user_id
    db.session.delete(item)
    db.session.commit()

    # Clean up now-empty folders
    _cleanup_empty_folders(user_id)

    flash("Item deleted.", "success")
    return redirect(url_for("items.list_items"))


@items_bp.route("/stores", methods=["GET"])
@login_required
def list_stores():
    """
    Simple helper page listing stores for a given country code.
    """
    country = request.args.get("country", "").strip()
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


@items_bp.route("/bulk-edit", methods=["POST"])
@login_required
def bulk_edit():
    """
    Show bulk edit form for selected items.
    """
    item_ids = request.form.getlist("item_ids")
    if not item_ids:
        flash("No items selected for bulk edit.", "warning")
        return redirect(url_for("items.list_items"))

    try:
        ids_int = [int(x) for x in item_ids]
    except ValueError:
        flash("Invalid item selection.", "danger")
        return redirect(url_for("items.list_items"))

    query = Item.query.filter(Item.id.in_(ids_int))

    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    items = query.all()
    if not items:
        flash("No items found for bulk edit.", "warning")
        return redirect(url_for("items.list_items"))

    categories = _get_or_create_folder_for_user(current_user.id, None)

    return render_template(
        "items/bulk_edit.html",
        items=items,
        categories=categories,
    )


@items_bp.route("/bulk-edit/submit", methods=["POST"])
@login_required
def bulk_edit_submit():
    """
    Apply bulk changes to items: active flag, folder, notifications, tags.
    """
    item_ids = request.form.getlist("item_ids")
    if not item_ids:
        flash("No items selected.", "warning")
        return redirect(url_for("items.list_items"))

    try:
        ids_int = [int(x) for x in item_ids]
    except ValueError:
        flash("Invalid item selection.", "danger")
        return redirect(url_for("items.list_items"))

    query = Item.query.filter(Item.id.in_(ids_int))
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    items = query.all()
    if not items:
        flash("No items found for bulk update.", "warning")
        return redirect(url_for("items.list_items"))

    is_active_raw = request.form.get("is_active")
    active_value = _cast_bool(is_active_raw)

    folder_name = request.form.get("folder_name_hidden", "").strip()

    notify_threshold_raw = request.form.get("notify_threshold", "").strip()
    notify_enabled_raw = request.form.get("notify_enabled")
    notify_enabled_value = _cast_bool(notify_enabled_raw)

    # Tags: if the field is left completely empty, we keep existing tags.
    # If filled with text (even just commas) we overwrite for all selected items.
    tags_raw = request.form.get("tags", None)
    if tags_raw is not None:
        tags_raw = tags_raw.strip()
    tag_names_for_bulk = None  # None => do not touch tags
    if tags_raw is not None:
        if tags_raw == "":
            tag_names_for_bulk = []  # explicit clear for all selected
        else:
            tag_names_for_bulk = _parse_tag_names(tags_raw)

    notify_threshold = _cast_int(notify_threshold_raw)

    for it in items:
        if active_value is not None:
            it.is_active = active_value

        if folder_name:
            it.folder = _get_or_create_folder_for_user(it.user_id, folder_name)
        elif folder_name == "":
            # If user explicitly chose "no folder"
            it.folder = None

        if notify_threshold_raw != "":
            it.notify_threshold = notify_threshold
        if notify_enabled_value is not None:
            it.notify_enabled = notify_enabled_value

        # Apply tags if requested
        if tag_names_for_bulk is not None:
            if tag_names_for_bulk:
                it.tags = _get_or_create_tags_for_user(it.user_id, tag_names_for_bulk)
            else:
                it.tags = []

    db.session.commit()
    flash("Bulk changes applied.", "success")
    return redirect(url_for("items.list_items"))


@items_bp.route("/<int:item_id>", methods=["GET"])
@login_required
def detail(item_id: int):
    """
    Show detail page with history chart and live availability.
    """
    item = Item.query.get_or_404(item_id)

    if not _require_edit_permission(item) and not current_user.is_admin:
        flash("You are not allowed to view this item.", "danger")
        return redirect(url_for("items.list_items"))

    # History range selection
    range_str = request.args.get("range", "30")
    try:
        days = int(range_str)
    except ValueError:
        days = 30

    since = datetime.utcnow() - timedelta(days=days)
    history = (
        AvailabilitySnapshot.query.filter_by(item_id=item.id)
        .filter(AvailabilitySnapshot.timestamp >= since)
        .order_by(AvailabilitySnapshot.timestamp.asc())
        .all()
    )

    labels = [h.timestamp.strftime("%Y-%m-%d %H:%M") for h in history]
    stocks = [h.total_stock if h.total_stock is not None else 0 for h in history]

    chart_labels = labels
    chart_datasets = [
        {
            "label": "Total stock",
            "data": stocks,
        }
    ]

    # Live per-store availability (does not modify DB)
    live_data, live_error = get_live_availability_for_item(item)

    return render_template(
        "items/detail.html",
        item=item,
        history=history,
        chart_labels=chart_labels,
        chart_datasets=chart_datasets,
        range_days=days,
        live_data=live_data,
        live_error=live_error,
    )


@items_bp.route("/<int:item_id>/check", methods=["POST"])
@login_required
def check_single(item_id: int):
    item = Item.query.get_or_404(item_id)

    if not _require_edit_permission(item):
        flash("You are not allowed to update this item.", "danger")
        return redirect(url_for("items.list_items"))

    ok, error = check_item(item)
    if ok:
        flash("Availability updated.", "success")
    else:
        flash(f"Check failed: {error}", "danger")

    return redirect(url_for("items.detail", item_id=item.id))


@items_bp.route("/import-export", methods=["GET"])
@login_required
def import_export_page():
    categories = _get_or_create_folder_for_user(current_user.id, None)
    return render_template("items/import_export.html", categories=categories)


@items_bp.route("/import-preview", methods=["POST"])
@login_required
def import_preview():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("No file uploaded.", "danger")
        return redirect(url_for("items.import_export_page"))

    try:
        rows, columns = _parse_uploaded_table(upload)
    except Exception as e:  # pragma: no cover - user file issues
        current_app.logger.exception("Import parse failed")
        flash(f"Failed to parse file: {e}", "danger")
        return redirect(url_for("items.import_export_page"))

    rows_preview = rows[:20]

    categories = _get_or_create_folder_for_user(current_user.id, None)

    return render_template(
        "items/import_preview.html",
        columns=columns,
        rows_preview=rows_preview,
        categories=categories,
    )


@items_bp.route("/import-submit", methods=["POST"])
@login_required
def import_submit():
    """
    Final step of import: user has mapped columns, chooses folder mode,
    and we create items.

    NOTE: tags are not imported yet – they can be added later via the UI.
    """
    encoded_rows = request.form.get("encoded_rows")
    if not encoded_rows:
        flash("Missing encoded_rows data.", "danger")
        return redirect(url_for("items.import_export_page"))

    try:
        rows = json.loads(encoded_rows)
    except json.JSONDecodeError:
        flash("Failed to decode row data.", "danger")
        return redirect(url_for("items.import_export_page"))

    map_name = request.form.get("map_name")
    map_product_id = request.form.get("map_product_id")
    map_country_code = request.form.get("map_country_code")
    map_store_ids = request.form.get("map_store_ids")
    map_active = request.form.get("map_active")
    map_notify_threshold = request.form.get("map_notify_threshold")

    folder_mode = request.form.get("folder_mode", "none")
    existing_folder = request.form.get("existing_folder")
    new_folder = request.form.get("new_folder")

    if not (map_name and map_product_id and map_country_code):
        flash("Name, product ID and country code mappings are required.", "danger")
        return redirect(url_for("items.import_export_page"))

    folder = None
    if folder_mode == "existing" and existing_folder:
        folder = _get_or_create_folder_for_user(current_user.id, existing_folder)
    elif folder_mode == "new" and new_folder:
        folder = _get_or_create_folder_for_user(current_user.id, new_folder)

    created_count = 0
    for row in rows:
        name = str(row.get(map_name, "")).strip()
        product_id = str(row.get(map_product_id, "")).strip()
        country_code = str(row.get(map_country_code, "")).strip().lower()

        if not name or not product_id or not country_code:
            continue

        store_ids = None
        if map_store_ids:
            store_raw = str(row.get(map_store_ids, "")).strip()
            store_ids = store_raw or None

        is_active = True
        if map_active:
            active_raw = str(row.get(map_active, "")).strip()
            parsed_active = _cast_bool(active_raw)
            if parsed_active is not None:
                is_active = parsed_active

        notify_threshold = None
        notify_enabled = False
        if map_notify_threshold:
            thr_raw = str(row.get(map_notify_threshold, "")).strip()
            if thr_raw:
                try:
                    notify_threshold = int(thr_raw)
                    notify_enabled = True
                except ValueError:
                    pass

        item = Item(
            user_id=current_user.id,
            name=name,
            product_id=product_id.replace(".", "").replace(" ", ""),
            country_code=country_code,
            store_ids=store_ids,
            is_active=is_active,
            folder=folder,
            notify_threshold=notify_threshold,
            notify_enabled=notify_enabled,
        )
        db.session.add(item)
        created_count += 1

    db.session.commit()
    flash(f"Imported {created_count} items.", "success")
    return redirect(url_for("items.list_items"))


@items_bp.route("/export", methods=["POST"])
@login_required
def export_items():
    """
    Export current user's items (or admin-filtered view) to CSV.
    Includes tags as a comma-separated string.
    """
    item_ids = request.form.getlist("item_ids")
    fmt = request.form.get("format", "csv")

    query = Item.query
    if item_ids:
        try:
            ids_int = [int(x) for x in item_ids]
            query = query.filter(Item.id.in_(ids_int))
        except ValueError:
            flash("Invalid item selection for export.", "danger")
            return redirect(url_for("items.list_items"))

    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    items = query.order_by(Item.created_at.asc()).all()

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "name",
            "product_id",
            "country_code",
            "store_ids",
            "is_active",
            "folder",
            "notify_threshold",
            "notify_enabled",
            "last_stock",
            "last_probability",
            "last_checked",
            "tags",
        ],
    )
    writer.writeheader()

    for it in items:
        tags_str = ", ".join(t.name for t in it.tags) if it.tags else ""
        writer.writerow(
            {
                "id": it.id,
                "name": it.name,
                "product_id": it.product_id,
                "country_code": it.country_code,
                "store_ids": it.store_ids or "",
                "is_active": "1" if it.is_active else "0",
                "folder": it.folder.name if it.folder else "",
                "notify_threshold": it.notify_threshold
                if it.notify_threshold is not None
                else "",
                "notify_enabled": "1" if it.notify_enabled else "0",
                "last_stock": it.last_stock if it.last_stock is not None else "",
                "last_probability": it.last_probability or "",
                "last_checked": it.last_checked.isoformat(),
                "created_at": it.created_at.isoformat()
                if it.last_checked
                else "",
                "tags": tags_str,
            }
        )

    output.seek(0)
    filename = f"items_export_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )
