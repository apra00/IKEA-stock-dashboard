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
    session,
    send_file,
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


# -----------------------------
# Helpers / Permissions
# -----------------------------
def _require_edit_permission():
    if not current_user.can_edit_items:
        flash("You are not authorized to modify items.", "danger")
        return False
    return True

def _get_or_create_folder_for_user(user_id: int, name: str | None):
    if not name:
        if current_user.is_admin:
            all_folders = Folder.query.order_by(Folder.name.asc()).all()
            # Deduplicate by name (folders are per-user)
            seen = set()
            uniq = []
            for f in all_folders:
                if f.name not in seen:
                    uniq.append(f)
                    seen.add(f.name)
            if len(uniq) > 0:
                return uniq
        else:
            folder = Folder.query.filter_by(user_id=current_user.id).order_by(Folder.name.asc()).all()
            if folder:
                return folder
    clean_name = name.strip()
    folder = Folder(user_id=user_id, name=clean_name)
    db.session.add(folder)
    db.session.flush()
    return folder


def _cleanup_empty_folders(user_id: int | None = None):
    query = Folder.query
    if user_id is not None:
        query = query.filter_by(user_id=user_id)

    folders = query.all()
    for f in folders:
        if not f.items:
            db.session.delete(f)


def _parse_uploaded_table(file_storage, has_header: bool = True) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Parse CSV or XLSX uploaded file into (columns, rows).

    - Always reads everything as strings to avoid Excel auto-typing issues.
    - If has_header=False, first row is treated as data and columns
      become Column 1, Column 2, ...
    """
    filename = (file_storage.filename or "").lower()

    if filename.endswith(".csv"):
        # Read as strings, allow headerless
        df = pd.read_csv(
            file_storage,
            dtype=str,
            header=0 if has_header else None,
            keep_default_na=False,
        )
        df = df.fillna("")

    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        df = pd.read_excel(
            file_storage,
            dtype=str,
            header=0 if has_header else None,
        )
        df = df.fillna("")

    else:
        raise ValueError("Unsupported file type. Use CSV or Excel (.xlsx).")

    # If no header, create placeholder names
    if not has_header:
        df.columns = [f"Column {i+1}" for i in range(len(df.columns))]
    else:
        df.columns = [str(c).strip() for c in df.columns]

    cols = list(df.columns.astype(str))
    rows = df.to_dict(orient="records")
    rows = [{str(k): ("" if v is None else str(v)) for k, v in r.items()} for r in rows]

    return cols, rows


def _cast_bool(val: Any) -> bool:
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _cast_int(val: Any) -> int | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(str(val).strip()))
    except Exception:
        return None


# -----------------------------
# Items list
# -----------------------------
@items_bp.route("/")
@login_required
def list_items():
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
        "owner": None,
    }

    sort_by = request.args.get("sort") or session.get("items_sort_by", "name")
    sort_dir = request.args.get("dir") or session.get("items_sort_dir", "asc")

    if sort_by not in allowed_sort_columns:
        sort_by = "name"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"

    session["items_sort_by"] = sort_by
    session["items_sort_dir"] = sort_dir

    if current_user.is_admin:
        query = Item.query.outerjoin(Folder)
    else:
        query = Item.query.filter_by(user_id=current_user.id).outerjoin(Folder)

    if sort_by == "folder":
        sort_column = Folder.name
    elif sort_by == "owner":
        if current_user.is_admin:
            from ..models import User
            query = query.join(User)
            sort_column = User.username
        else:
            sort_column = Item.name
    else:
        sort_column = allowed_sort_columns[sort_by]

    if sort_dir == "desc":
        sort_column = sort_column.desc()

    items = query.order_by(sort_column).all()

    folder_groups: dict[str, list[Item]] = {}
    for item in items:
        group_name = item.folder.name if item.folder else "No category"
        folder_groups.setdefault(group_name, []).append(item)

    return render_template(
        "items/list.html",
        folders=folder_groups,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


# -----------------------------
# Single item CRUD (unchanged)
# -----------------------------
@items_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_item():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        product_id = request.form.get("product_id", "").strip()
        country_code = request.form.get("country_code", "").strip().upper()
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
                return render_template("items/form.html", item=None, categories=categories)

        folder = _get_or_create_folder_for_user(current_user.id, folder_name)

        item = Item(
            user_id=current_user.id,
            name=name,
            product_id=product_id,
            country_code=country_code,
            store_ids=store_ids,
            is_active=is_active,
            notify_enabled=notify_enabled,
            notify_threshold=notify_threshold,
            folder=folder,
        )

        if not name or not product_id or not country_code:
            flash("Name, product ID and country code are required.", "danger")
        else:
            db.session.add(item)
            db.session.commit()
            flash("Item added.", "success")
            return redirect(url_for("items.list_items"))

    categories = (
        Folder.query.filter_by(user_id=current_user.id).order_by(Folder.name.asc()).all()
    )
    return render_template("items/form.html", item=None, categories=categories)


@items_bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_item(item_id):
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    item = Item.query.get_or_404(item_id)
    if not current_user.is_admin and item.user_id != current_user.id:
        flash("You are not allowed to edit this item.", "danger")
        return redirect(url_for("items.list_items"))

    if request.method == "POST":
        item.name = request.form.get("name", "").strip()
        item.product_id = request.form.get("product_id", "").strip()
        item.country_code = request.form.get("country_code", "").strip().upper()
        item.store_ids = request.form.get("store_ids", "").strip()
        folder_name = request.form.get("folder_name", "").strip()

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
            _cleanup_empty_folders(item.user_id)
            db.session.commit()
            flash("Item updated.", "success")
            return redirect(url_for("items.list_items"))

    categories = (
        Folder.query.filter_by(user_id=item.user_id).order_by(Folder.name.asc()).all()
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

    user_id = item.user_id
    db.session.delete(item)
    _cleanup_empty_folders(user_id)
    db.session.commit()
    flash("Item deleted.", "info")
    return redirect(url_for("items.list_items"))


@items_bp.route("/stores/<country_code>")
@login_required
def stores(country_code):
    stores = get_stores_for_country(country_code.upper())
    return render_template("items/stores.html", stores=stores, country_code=country_code)


# -----------------------------
# Bulk Update / Bulk Edit (unchanged)
# -----------------------------
@items_bp.route("/bulk", methods=["POST"])
@login_required
def bulk_update():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    raw_ids = request.form.getlist("item_ids")
    action = request.form.get("bulk_action", "").strip()

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

    query = Item.query.filter(Item.id.in_(item_ids))
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    items = query.all()
    if not items:
        flash("You are not allowed to modify the selected items.", "danger")
        return redirect(url_for("items.list_items"))

    if action == "edit":
        ids_str = ",".join(str(i.id) for i in items)
        return redirect(url_for("items.bulk_edit", ids=ids_str))

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
        _cleanup_empty_folders()

    db.session.commit()

    if action == "delete":
        flash(f"Deleted {deleted} items.", "success")
    else:
        flash(f"Updated {updated} items.", "success")

    return redirect(url_for("items.list_items"))


@items_bp.route("/bulk-edit")
@login_required
def bulk_edit():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    ids_str = request.args.get("ids", "")
    try:
        item_ids = [int(x) for x in ids_str.split(",") if x.strip()]
    except ValueError:
        item_ids = []

    if not item_ids:
        flash("No items selected.", "danger")
        return redirect(url_for("items.list_items"))

    query = Item.query.filter(Item.id.in_(item_ids))
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    items = query.all()
    if not items:
        flash("You are not allowed to edit those items.", "danger")
        return redirect(url_for("items.list_items"))

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

    if not current_user.is_admin:
        items = [it for it in items if it.user_id == current_user.id]
        if not items:
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
            return redirect(url_for("items.bulk_edit", ids=",".join(item_ids)))

    for it in items:
        it.is_active = is_active
        it.notify_enabled = notify_enabled
        it.notify_threshold = notify_threshold

        if folder_name:
            it.folder = _get_or_create_folder_for_user(it.user_id, folder_name)
        else:
            it.folder = None

    _cleanup_empty_folders(items[0].user_id)
    db.session.commit()
    flash("Bulk edit applied.", "success")
    return redirect(url_for("items.list_items"))


def _extract_store_lines_from_snapshots(chart_history, tracked_store_ids):
    """
    From AvailabilitySnapshot.raw_json, build per-store time series.
    Returns (store_order, store_names, series_dict).

    series_dict[store_id] = [stock_or_None_per_timestamp]
    """
    store_names = {}
    series = {sid: [] for sid in tracked_store_ids}

    for snap in chart_history:
        raw = snap.raw_json
        try:
            data = json.loads(raw) if raw else []
        except Exception:
            data = []

        # Map store_id -> stock for this timestamp
        stocks_this_time = {}

        for entry in data or []:
            if not isinstance(entry, dict):
                continue

            store = entry.get("store") or {}
            store_id = (
                store.get("buCode")
                or store.get("id")
                or entry.get("storeId")
                or entry.get("storeCode")
                or entry.get("buCode")
                or entry.get("storeBuCode")
            )
            if store_id is None:
                continue
            store_id = str(store_id)

            stock_val = entry.get("stock")
            try:
                stock_val = int(stock_val) if stock_val is not None else None
            except Exception:
                stock_val = None

            stocks_this_time[store_id] = stock_val

            # Remember store display name if present
            store_name = store.get("name") or entry.get("storeName")
            if store_name and store_id not in store_names:
                store_names[store_id] = str(store_name)

        # append aligned values
        for sid in tracked_store_ids:
            series[sid].append(stocks_this_time.get(sid))

    store_order = tracked_store_ids[:]
    return store_order, store_names, series

# -----------------------------
# IMPORT FLOW
# -----------------------------
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
    if len(chart_history) > 500:
        chart_history = chart_history[-500:]

    chart_labels = [
        (h.timestamp or now).strftime("%Y-%m-%d %H:%M") for h in chart_history
    ]

    # -----------------------------
    # per-store datasets when multiple stores tracked
    # -----------------------------
    tracked_store_ids = (
        [s.strip() for s in (item.store_ids or "").split(",") if s.strip()]
        if item.store_ids
        else []
    )

    chart_datasets = []

    if len(tracked_store_ids) >= 2:
        store_order, store_names, series = _extract_store_lines_from_snapshots(
            chart_history, tracked_store_ids
        )
        for sid in store_order:
            chart_datasets.append(
                {
                    "label": store_names.get(sid, f"Store {sid}"),
                    "data": [v if v is not None else 0 for v in series[sid]],
                }
            )
    else:
        # fallback to total stock line
        chart_datasets = [
            {"label": "Total stock", "data": [h.total_stock or 0 for h in chart_history]}
        ]

    # Live per-store availability for detailed view
    live_data, live_error = get_live_availability_for_item(item)

    return render_template(
        "items/detail.html",
        item=item,
        history=history_changed,
        chart_history=chart_history,
        chart_labels=chart_labels,
        chart_datasets=chart_datasets,
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


# ============================================================
# IMPORT / EXPORT
# ============================================================

def _ensure_pandas():
    if pd is None:
        raise RuntimeError(
            "pandas is not installed. Add pandas and openpyxl to requirments.txt."
        )


def _read_import_file(file_storage):
    """
    Returns (rows, columns, error).
    rows: list[dict]
    columns: list[str]
    """
    _ensure_pandas()

    filename = secure_filename(file_storage.filename or "")
    if not filename:
        return [], [], "No file selected."

    ext = filename.rsplit(".", 1)[-1].lower()
    try:
        if ext in ("csv", "txt"):
            df = pd.read_csv(file_storage)
        elif ext in ("xlsx", "xls"):
            df = pd.read_excel(file_storage)
        else:
            return [], [], "Unsupported file type. Use CSV or Excel."
    except Exception as e:
        return [], [], f"Failed to parse file: {e}"

    # Normalize columns to string
    df.columns = [str(c).strip() for c in df.columns]
    columns = list(df.columns)

    # Limit import size for safety
    if len(df) > 5000:
        df = df.head(5000)

    # Replace NaN with None
    rows = df.where(pd.notnull(df), None).to_dict(orient="records")
    return rows, columns, None


@items_bp.route("/import-export", methods=["GET"])
@login_required
def import_export_page():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    categories = _get_or_create_folder_for_user(current_user.id, None)

    return render_template("items/import_export.html", categories=categories)


@items_bp.route("/import/preview", methods=["POST"])
@login_required
def import_preview():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    file = request.files.get("file")
    has_header = request.form.get("has_header") == "on"

    if not file or not file.filename:
        flash("Please choose a CSV or Excel file to import.", "danger")
        return redirect(url_for("items.import_export_page"))

    try:
        cols, rows = _parse_uploaded_table(file, has_header=has_header)
    except Exception as e:
        flash(str(e), "danger")
        return redirect(url_for("items.import_export_page"))

    if not cols or not rows:
        flash("No data found in file.", "danger")
        return redirect(url_for("items.import_export_page"))

    session["import_rows"] = rows[:2000]
    session["import_cols"] = cols

    categories = _get_or_create_folder_for_user(current_user.id, None)


    preview_rows = rows[:20]

    return render_template(
        "items/import_preview.html",
        columns=cols,
        rows_preview=preview_rows,
        categories=categories,
        has_header=has_header,
    )


@items_bp.route("/import/submit", methods=["POST"])
@login_required
def import_submit():
    if not _require_edit_permission():
        return redirect(url_for("items.list_items"))

    rows: List[Dict[str, Any]] = session.get("import_rows") or []
    cols: List[str] = session.get("import_cols") or []
    if not rows or not cols:
        flash("Import session expired. Please upload again.", "danger")
        return redirect(url_for("items.import_export_page"))

    map_name = request.form.get("map_name") or ""
    map_product_id = request.form.get("map_product_id") or ""
    map_country = request.form.get("map_country_code") or ""
    map_stores = request.form.get("map_store_ids") or ""
    map_active = request.form.get("map_is_active") or ""
    map_notify_enabled = request.form.get("map_notify_enabled") or ""
    map_notify_threshold = request.form.get("map_notify_threshold") or ""

    # Manual defaults (used when mapping not provided)
    manual_country = (request.form.get("manual_country_code") or "").strip().upper()
    manual_store_ids = (request.form.get("manual_store_ids") or "").strip()
    manual_notify_enabled = request.form.get("manual_notify_enabled") == "on"
    manual_notify_threshold = _cast_int(request.form.get("manual_notify_threshold"))

    # Require Name + Product mapping. Country required overall.
    if not map_name or not map_product_id:
        flash("You must map Name and Product ID columns.", "danger")
        return redirect(url_for("items.import_preview"))

    if not map_country and not manual_country:
        flash("Country is required: map a Country Code column or set a default country code.", "danger")
        return redirect(url_for("items.import_preview"))


    folder_mode = request.form.get("folder_mode", "none")
    folder_name = ""
    if folder_mode == "existing":
        folder_name = request.form.get("existing_folder", "").strip()
    elif folder_mode == "new":
        folder_name = request.form.get("new_folder", "").strip()
    else:
        folder_name = ""

    folder = _get_or_create_folder_for_user(current_user.id, folder_name) if folder_name else None

    created = 0
    skipped = 0

    for r in rows:
        try:
            name = str(r.get(map_name, "")).strip()
            product_id = str(r.get(map_product_id, "")).strip()

            # Country from mapped column OR manual default
            country_code = (
                str(r.get(map_country, "")).strip().upper()
                if map_country
                else manual_country
            )

            if not name or not product_id or not country_code:
                skipped += 1
                continue

            # Stores from mapped column OR manual default
            store_ids = (
                str(r.get(map_stores, "")).strip()
                if map_stores
                else manual_store_ids
            )

            is_active = _cast_bool(r.get(map_active)) if map_active else True

            # Notify enabled / threshold from mapped column OR manual default
            notify_enabled = (
                _cast_bool(r.get(map_notify_enabled))
                if map_notify_enabled
                else manual_notify_enabled
            )

            notify_threshold = (
                _cast_int(r.get(map_notify_threshold))
                if map_notify_threshold
                else manual_notify_threshold
            )


            item = Item(
                user_id=current_user.id,
                name=name,
                product_id=product_id,
                country_code=country_code,
                store_ids=store_ids,
                is_active=is_active,
                notify_enabled=notify_enabled,
                notify_threshold=notify_threshold,
                folder=folder,
            )
            db.session.add(item)
            created += 1
        except Exception:
            skipped += 1

    db.session.commit()

    session.pop("import_rows", None)
    session.pop("import_cols", None)

    flash(f"Import finished. Created: {created}, Skipped: {skipped}.", "success")
    return redirect(url_for("items.list_items"))


# -----------------------------
# EXPORT (unchanged)
# -----------------------------
@items_bp.route("/export", methods=["POST"])
@login_required
def export_items():
    """
    Export selected items in CSV / Excel / JSON.
    """
    item_ids = request.form.getlist("item_ids")
    fmt = (request.form.get("format") or "csv").lower()

    if not item_ids:
        flash("No items selected for export.", "warning")
        return redirect(url_for("items.list_items"))

    query = Item.query.filter(Item.id.in_(item_ids))
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)

    items = query.all()
    if not items:
        flash("You are not allowed to export these items.", "danger")
        return redirect(url_for("items.list_items"))

    data = []
    for it in items:
        data.append(
            {
                "id": it.id,
                "name": it.name,
                "product_id": it.product_id,
                "country_code": it.country_code,
                "store_ids": it.store_ids or "",
                "is_active": bool(it.is_active),
                "notify_enabled": bool(it.notify_enabled),
                "notify_threshold": it.notify_threshold if it.notify_threshold is not None else "",
                "folder_name": it.folder.name if it.folder else "",
                "added_at": it.added_at.isoformat() if it.added_at else "",
                "last_stock": it.last_stock if it.last_stock is not None else "",
                "last_probability": it.last_probability or "",
                "last_checked": it.last_checked.isoformat() if it.last_checked else "",
            }
        )

    filename_base = f"ikea-items-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    if fmt == "json":
        buf = io.BytesIO(json.dumps(data, indent=2).encode("utf-8"))
        buf.seek(0)
        return send_file(
            buf,
            mimetype="application/json",
            as_attachment=True,
            download_name=f"{filename_base}.json",
        )

    _ensure_pandas()
    df = pd.DataFrame(data)

    if fmt == "xlsx":
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Items")
        out.seek(0)
        return send_file(
            out,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"{filename_base}.xlsx",
        )

    # default CSV
    out = io.StringIO()
    df.to_csv(out, index=False)
    buf = io.BytesIO(out.getvalue().encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{filename_base}.csv",
    )