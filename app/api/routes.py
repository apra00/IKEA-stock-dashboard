from flask import Blueprint, request, jsonify, current_app, abort
from ..models import Item
from ..ikea_service import check_item, check_all_active_items

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _require_api_key():
    """
    Require a valid API key in X-API-Key header or ?api_key= query param.
    """
    expected = current_app.config.get("WEBHOOK_API_KEY")
    if not expected:
        abort(403)

    provided = request.headers.get("X-API-Key") or request.args.get("api_key")
    if not provided or provided != expected:
        abort(403)


@api_bp.route("/check", methods=["POST"])
def webhook_check():
    """
    Webhook endpoint to trigger stock checks.
    - POST /api/check  with no body: check all active items
    - POST /api/check  { "item_id": 123 } : check single item by id
    - POST /api/check  { "product_id": "80213074" } : check single item by product_id
    """
    _require_api_key()

    data = request.get_json(silent=True) or {}
    item_id = data.get("item_id")
    product_id = data.get("product_id")

    if item_id:
        item = Item.query.get(item_id)
        if not item:
            return jsonify({"status": "error", "error": "Item not found"}), 404
        ok, err = check_item(item)
        return jsonify({"status": "ok" if ok else "error", "error": err}), 200

    if product_id:
        item = Item.query.filter_by(product_id=str(product_id)).first()
        if not item:
            return jsonify({"status": "error", "error": "Item not found"}), 404
        ok, err = check_item(item)
        return jsonify({"status": "ok" if ok else "error", "error": err}), 200

    # No specifics: check all active items
    ok_count, failed_count = check_all_active_items()
    return jsonify(
        {
            "status": "ok",
            "checked": ok_count + failed_count,
            "ok": ok_count,
            "failed": failed_count,
        }
    ), 200
