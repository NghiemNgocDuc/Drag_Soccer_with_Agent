import logging
import requests
from config import PRODUCTBRIDGE_API_KEY, PRODUCTBRIDGE_BOARD_ID

BASE_URL = "https://api.productbridge.io/api/external/v1"

_headers = {"Content-Type": "application/json"}


def _post(endpoint: str, payload: dict) -> dict | None:
    if not PRODUCTBRIDGE_API_KEY:
        logging.warning("ProductBridge API key not set — skipping request")
        return None
    body = {"api_key": PRODUCTBRIDGE_API_KEY, **payload}
    try:
        r = requests.post(f"{BASE_URL}/{endpoint}", json=body, headers=_headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.error("ProductBridge API error [%s]: %s", endpoint, e)
        return None


def ping() -> dict | None:
    return _post("ping", {})


def submit_feedback(
    title: str,
    description: str,
    user_email: str = "",
    board_id: str = "",
) -> dict | None:
    payload = {
        "title": title,
        "description": description,
        "board_id": board_id or PRODUCTBRIDGE_BOARD_ID,
    }
    if user_email:
        payload["user_email"] = user_email
    return _post("feedback-posts/create", payload)


def list_feedback(board_id: str = "") -> list:
    bid = board_id or PRODUCTBRIDGE_BOARD_ID
    data = _post("feedback-posts/list", {"board_id": bid, "limit": 50})
    if data:
        return data.get("items", [])
    return []


def list_boards() -> list:
    data = _post("feedback-boards/list", {})
    if data:
        return data.get("items", [])
    return []
