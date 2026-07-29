import logging
from config import SENTRY_DSN

_initialized = False


def init():
    global _initialized
    if _initialized or not SENTRY_DSN:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[
                FlaskIntegration(),
                RedisIntegration(),
            ],
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
        _initialized = True
        logging.info("Sentry initialized")
    except Exception as e:
        logging.warning("Failed to init Sentry: %s", e)


def capture_exception(exc: Exception, context: dict | None = None):
    if not _initialized:
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            if context:
                for k, v in context.items():
                    scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass
