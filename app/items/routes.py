from datetime import datetime, timedelta
import json
import io

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
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import Item, AvailabilitySnapshot, Folder
from ..ikea_service import (
    check_item,
    check_all_active_items,
    get_stores_for_country,
    get_live_availability_for_item,
)

# Optional deps for import/export
try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None

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
    _cleanup_empty_folders(user_id)
    db.session.commit()
    flash("Item deleted.", "info")
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

    categories = (
        Folder.query.filter_by(user_id=current_user.id)
        .order_by(Folder.name.asc())
        .all()
    )

    return render_template("items/import_export.html", categories=categories)


@items_bp.route("/import/preview", methods=["POST"])
@login_required
def import_preview():
    if not _require_edit_permission():
        return redirect(url_for("items.import_export_page"))

    file_storage = request.files.get("import_file")
    if not file_storage:
        flash("Please choose a file.", "danger")
        return redirect(url_for("items.import_export_page"))

    rows, columns, error = _read_import_file(file_storage)
    if error:
        flash(error, "danger")
        return redirect(url_for("items.import_export_page"))

    categories = (
        Folder.query.filter_by(user_id=current_user.id)
        .order_by(Folder.name.asc())
        .all()
    )

    # Give a small preview in UI, keep all rows in hidden json
    preview_rows = rows[:20]

    return render_template(
        "items/import_preview.html",
        columns=columns,
        rows_json=json.dumps(rows),
        preview_rows=preview_rows,
        categories=categories,
        filename=file_storage.filename,
    )


@items_bp.route("/import/commit", methods=["POST"])
@login_required
def import_commit():
    if not _require_edit_permission():
        return redirect(url_for("items.import_export_page"))

    rows_json = request.form.get("rows_json", "")
    mapping_json = request.form.get("mapping_json", "")

    try:
        rows = json.loads(rows_json) if rows_json else []
        mapping = json.loads(mapping_json) if mapping_json else {}
    except Exception:
        flash("Invalid import payload.", "danger")
        return redirect(url_for("items.import_export_page"))

    if not rows:
        flash("No rows to import.", "warning")
        return redirect(url_for("items.import_export_page"))

    # folder choice
    folder_choice = request.form.get("folder_choice", "__none__")
    new_folder_name = request.form.get("new_folder_name", "").strip()
    folder_name = None
    if folder_choice == "__new__":
        folder_name = new_folder_name or None
    elif folder_choice == "__none__":
        folder_name = None
    else:
        folder_name = folder_choice

    folder_obj = None
    if folder_name:
        folder_obj = _get_or_create_folder_for_user(current_user.id, folder_name)

    created = 0
    skipped = 0
    errors = 0

    # Helper to fetch a value from row based on mapping
    def val(row, field):
        col = mapping.get(field)
        if not col or col == "__ignore__":
            return None
        return row.get(col)

    for row in rows:
        try:
            name = (val(row, "name") or "").strip()
            product_id = (val(row, "product_id") or "").strip()
            country_code = (val(row, "country_code") or "").strip().lower()
            store_ids = (val(row, "store_ids") or None)

            if not name or not product_id or not country_code:
                skipped += 1
                continue

            item = Item(
                name=name,
                product_id=str(product_id).replace(".", ""),
                country_code=country_code,
                store_ids=str(store_ids).strip() if store_ids else None,
                user_id=current_user.id,
                is_active=True,
            )

            # optional fields
            is_active_raw = val(row, "is_active")
            if is_active_raw is not None:
                s = str(is_active_raw).strip().lower()
                item.is_active = s in ("1", "true", "yes", "y", "on")

            notify_enabled_raw = val(row, "notify_enabled")
            if notify_enabled_raw is not None:
                s = str(notify_enabled_raw).strip().lower()
                item.notify_enabled = s in ("1", "true", "yes", "y", "on")

            notify_th_raw = val(row, "notify_threshold")
            if notify_th_raw is not None and str(notify_th_raw).strip() != "":
                try:
                    item.notify_threshold = int(float(notify_th_raw))
                except Exception:
                    item.notify_threshold = None

            if folder_obj:
                item.folder = folder_obj
            else:
                # if they mapped folder_name as column, allow per-row folders
                row_folder = val(row, "folder_name")
                if row_folder:
                    fobj = _get_or_create_folder_for_user(
                        current_user.id, str(row_folder).strip()
                    )
                    item.folder = fobj

            db.session.add(item)
            created += 1
        except Exception:
            errors += 1

    db.session.commit()

    flash(
        f"Import finished. Created {created}, skipped {skipped}, errors {errors}.",
        "success" if created else "warning",
    )
    return redirect(url_for("items.list_items"))


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
