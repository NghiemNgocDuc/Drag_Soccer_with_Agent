import logging
import requests
from config import CLERK_SECRET_KEY

CLERK_API_URL = "https://api.clerk.com/v1"

_headers = {
    "Authorization": f"Bearer {CLERK_SECRET_KEY}",
    "Content-Type": "application/json",
}


def verify_session(session_token: str) -> dict | None:
    if not CLERK_SECRET_KEY:
        logging.warning("Clerk secret key not set")
        return None
    try:
        r = requests.post(
            f"{CLERK_API_URL}/sessions/verify",
            json={"token": session_token},
            headers=_headers,
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
        logging.warning("Clerk verify failed: %s", r.text)
        return None
    except Exception as e:
        logging.error("Clerk verify error: %s", e)
        return None


def get_user(user_id: str) -> dict | None:
    if not CLERK_SECRET_KEY:
        return None
    try:
        r = requests.get(
            f"{CLERK_API_URL}/users/{user_id}",
            headers=_headers,
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        logging.error("Clerk get_user error: %s", e)
        return None


def create_user(email: str, password: str, username: str) -> dict | None:
    if not CLERK_SECRET_KEY:
        return None
    try:
        r = requests.post(
            f"{CLERK_API_URL}/users",
            json={
                "email_address": [email],
                "password": password,
                "username": username,
            },
            headers=_headers,
            timeout=10,
        )
        if r.status_code in (200, 201):
            return r.json()
        logging.warning("Clerk create_user failed: %s", r.text)
        return None
    except Exception as e:
        logging.error("Clerk create_user error: %s", e)
        return None
