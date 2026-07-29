import logging
from config import RESEND_API_KEY, RESEND_FROM_EMAIL

_client = None

def get_client():
    global _client
    if _client is None and RESEND_API_KEY:
        try:
            import resend
            resend.api_key = RESEND_API_KEY
            _client = resend
        except Exception as e:
            logging.warning("Failed to init Resend: %s", e)
    return _client


def send_email(to: str, subject: str, html: str) -> bool:
    client = get_client()
    if not client:
        logging.warning("Resend not configured — skipping email to %s", to)
        return False
    try:
        client.Emails.send({
            "from": RESEND_FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        return True
    except Exception as e:
        logging.error("Resend send failed: %s", e)
        return False


def send_welcome(email: str, username: str) -> bool:
    return send_email(
        to=email,
        subject="Welcome to Agent Soccer!",
        html=f"<h2>Welcome, {username}!</h2><p>Thanks for joining Agent Soccer. Build your AI, play matches, and climb the leaderboard.</p>",
    )
