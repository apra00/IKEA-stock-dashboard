# app/ikea_service.py
import json
import subprocess
from datetime import datetime
from typing import List, Optional, Tuple

from flask import current_app
from .extensions import db
from .models import Item, AvailabilitySnapshot, User
from .email_utils import send_email


NODE_SUBPROCESS_TIMEOUT = 30  # seconds – avoid hanging Node subprocesses


def _run_node_checker(country: str, product_id: str, store_ids: Optional[List[str]]):
    """
    Calls ikea_client.js and returns (data, error_message).
    """
    store_arg = ",".join(store_ids) if store_ids else ""
    cmd = [
        "node",
        "ikea_client.js",
        country,
        product_id,
        store_arg,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=current_app.root_path + "/..",  # project root
            timeout=NODE_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, "Node process timed out."

    if proc.returncode != 0:
        return None, proc.stderr.strip() or "Node process failed."

    stdout = proc.stdout.strip()
    try:
        data = json.loads(stdout) if stdout else []
    except json.JSONDecodeError:
        return None, "Failed to parse JSON from Node output."

    return data, None


def _run_node_stores(country: str):
    """
    Calls ikea_stores.js and returns (data, error_message).
    """
    cmd = [
        "node",
        "ikea_stores.js",
        country,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=current_app.root_path + "/..",  # project root
            timeout=NODE_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, "Node process timed out."

    if proc.returncode != 0:
        return None, proc.stderr.strip() or "Node process failed."

    stdout = proc.stdout.strip()
    try:
        data = json.loads(stdout) if stdout else []
    except json.JSONDecodeError:
        return None, "Failed to parse JSON from Node output."

    return data, None


def parse_availability_summary(data: list) -> Tuple[int, str]:
    """
    Aggregate total stock and probability summary from ikea-availability-checker output.
    """
    total_stock = 0
    probabilities = set()

    for entry in data:
        if not entry:
            continue
        stock = entry.get("stock")
        if stock is not None:
            try:
                total_stock += int(stock)
            except (TypeError, ValueError):
                pass
        prob = entry.get("probability")
        if prob:
            probabilities.add(str(prob))

    prob_str = ", ".join(sorted(probabilities)) if probabilities else "UNKNOWN"
    return total_stock, prob_str


def _send_threshold_notification(
    item: Item, total_stock: int, prob_str: str, timestamp: datetime, direction: str
):
    """
    Send email notification when an item's stock crosses the configured threshold.

    Security/privacy: only notify the item's owner (and optionally admins),
    not every user in the system.
    """
    recipients = set()

    # Primary recipient: the item's owner
    if item.user and item.user.email:
        recipients.add(item.user.email)

    # Optionally, also notify admins that have an email configured
    admin_users = User.query.filter(
        User.role == "admin", User.email.isnot(None)
    ).all()
    for admin in admin_users:
        recipients.add(admin.email)

    if not recipients:
        return

    if direction == 'above':
        subject = f"IKEA stock above alert: {item.name} ({item.product_id})"
        body = (
            f"Stock for item '{item.name}' (product {item.product_id}) "
            f"has went above {item.notify_threshold} and now is at {total_stock}.\n\n"
            f"Owner: {item.user.username if item.user else 'Unknown'}\n"
            f"Folder: {item.folder.name if item.folder else 'None'}\n"
            f"Country: {item.country_code}\n"
            f"Stores filter: {item.store_ids or 'All stores in country'}\n"
            f"Probability summary: {prob_str}\n"
            f"Time (UTC): {timestamp:%Y-%m-%d %H:%M}\n"
            f"Threshold: {item.notify_threshold}\n"
        )
    
    if direction == 'bellow':
        subject = f"IKEA stock bellow alert: {item.name} ({item.product_id})"
        body = (
            f"Stock for item '{item.name}' (product {item.product_id}) "
            f"has went bellow {item.notify_bellow_threshold} and now is at {total_stock}.\n\n"
            f"Owner: {item.user.username if item.user else 'Unknown'}\n"
            f"Folder: {item.folder.name if item.folder else 'None'}\n"
            f"Country: {item.country_code}\n"
            f"Stores filter: {item.store_ids or 'All stores in country'}\n"
            f"Probability summary: {prob_str}\n"
            f"Time (UTC): {timestamp:%Y-%m-%d %H:%M}\n"
            f"Threshold: {item.notify_threshold}\n"
        )

    send_email(subject, body, list(recipients))


def check_item(item: Item):
    """
    Check availability for a single item; update item + insert history snapshot.
    Also handles threshold email notification if configured.
    """
    store_ids = (
        [s.strip() for s in (item.store_ids or "").split(",") if s.strip()]
        if item.store_ids
        else None
    )

    # Keep previous stock for threshold detection
    previous_stock = item.last_stock

    data, error = _run_node_checker(item.country_code, item.product_id, store_ids)
    timestamp = datetime.utcnow()

    if error or not data:
        # save snapshot with no data but mark probability
        snapshot = AvailabilitySnapshot(
            item=item,
            timestamp=timestamp,
            total_stock=None,
            probability_summary=f"ERROR: {error or 'No data'}",
            raw_json=None,
        )
        db.session.add(snapshot)

        item.last_stock = None
        item.last_probability = f"ERROR: {error or 'No data'}"
        item.last_checked = timestamp
        db.session.commit()
        return False, error or "No data"

    total_stock, prob_str = parse_availability_summary(data)
    snapshot = AvailabilitySnapshot(
        item=item,
        timestamp=timestamp,
        total_stock=total_stock,
        probability_summary=prob_str,
        raw_json=json.dumps(data),
    )
    db.session.add(snapshot)

    item.last_stock = total_stock
    item.last_probability = prob_str
    item.last_checked = timestamp

    # Threshold notification check for above
    should_notify = (
        item.notify_enabled
        and item.notify_threshold is not None
        and total_stock is not None
    )
    if should_notify:
        was_below = previous_stock is None or previous_stock < item.notify_threshold
        now_above = total_stock >= item.notify_threshold
        if was_below and now_above:
            _send_threshold_notification(item, total_stock, prob_str, timestamp, 'above')
            item.last_notified_at = timestamp
    
    # Threshold notification check for bellow
    should_notify = (
        item.notify_bellow_enabled
        and item.notify_bellow_threshold is not None
        and total_stock is not None
    )
    if should_notify:
        was_above = previous_stock is None or previous_stock >= item.notify_bellow_threshold
        now_bellow = total_stock < item.notify_bellow_threshold
        if was_above and now_bellow:
            _send_threshold_notification(item, total_stock, prob_str, timestamp, 'bellow')
            item.last_notified_bellow_at = timestamp

    db.session.commit()
    return True, None


def check_all_active_items(user=None):
    """
    Check all active items.
    - If user is None: check all active items in the system (used by webhook).
    - If user is provided and not admin: check only that user's active items.
    - If user is admin: check all active items.
    """
    query = Item.query.filter_by(is_active=True)

    if user is not None and not user.is_admin:
        query = query.filter_by(user_id=user.id)

    items = query.all()
    successes = 0
    failures = 0

    for item in items:
        ok, _ = check_item(item)
        if ok:
            successes += 1
        else:
            failures += 1

    return successes, failures


def get_stores_for_country(country: str):
    """
    Return list of stores for a given country using ikea-availability-checker.
    """
    data, error = _run_node_stores(country)
    if error or not data:
        return [], error or "No store data."
    return data, None


def get_live_availability_for_item(item: Item):
    """
    Get live per-store availability for an item without updating DB.
    Used for the item detail view to show store-by-store stock.
    """
    store_ids = (
        [s.strip() for s in (item.store_ids or "").split(",") if s.strip()]
        if item.store_ids
        else None
    )
    data, error = _run_node_checker(item.country_code, item.product_id, store_ids)
    return data or [], error
