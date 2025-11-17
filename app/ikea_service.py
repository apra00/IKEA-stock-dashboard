import json
import subprocess
from datetime import datetime
from typing import List, Optional, Tuple

from flask import current_app
from .extensions import db
from .models import Item, AvailabilitySnapshot, User
from .email_utils import send_email


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

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=current_app.root_path + "/..",  # project root
    )

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

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=current_app.root_path + "/..",  # project root
    )

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


def _send_threshold_notification(item: Item, total_stock: int, prob_str: str, timestamp: datetime):
    """
    Send email notification to all users who have an email set,
    when an item's stock crosses the configured threshold.
    """
    recipients = [u.email for u in User.query.filter(User.email.isnot(None)).all()]
    if not recipients:
        return

    subject = f"IKEA stock alert: {item.name} ({item.product_id})"
    body = (
        f"Stock for item '{item.name}' (product {item.product_id}) has reached {total_stock}.\n\n"
        f"Country: {item.country_code}\n"
        f"Stores filter: {item.store_ids or 'All stores in country'}\n"
        f"Probability summary: {prob_str}\n"
        f"Time (UTC): {timestamp:%Y-%m-%d %H:%M}\n"
        f"Threshold: {item.notify_threshold}\n"
    )

    send_email(subject, body, recipients)


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

    # Threshold notification check: from below to >= threshold
    should_notify = (
        item.notify_enabled
        and item.notify_threshold is not None
        and total_stock is not None
    )
    if should_notify:
        was_below = previous_stock is None or previous_stock < item.notify_threshold
        now_above = total_stock >= item.notify_threshold
        if was_below and now_above:
            _send_threshold_notification(item, total_stock, prob_str, timestamp)
            item.last_notified_at = timestamp

    db.session.commit()
    return True, None


def check_all_active_items():
    """
    Check all active items and append to history.
    """
    items = Item.query.filter_by(is_active=True).all()
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
    # data is already a list of store objects from ikea-availability-checker
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
