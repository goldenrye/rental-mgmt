"""Apple Push Notification service client for the iPhone app (token-based auth, HTTP/2)."""

import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.config import get_settings

logger = logging.getLogger(__name__)
HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
# Apple rejects provider tokens older than an hour and refreshes more often than every 20 minutes.
TOKEN_SECONDS = 45 * 60
TOKEN_PATTERN = re.compile(r"^(?:[0-9a-f]{2}){32,100}$")
_lock = threading.Lock()
_provider_token = (None, 0.0, None)
_client = None


class ApnsError(Exception):
    def __init__(self, status, reason):
        super().__init__(f"APNs {status} {reason}")
        self.status = status
        self.reason = reason

    @property
    def gone(self):
        """The token will never work again for this app."""
        return self.status == 410 or self.reason in ("Unregistered", "DeviceTokenNotForTopic")

    @property
    def wrong_environment(self):
        return self.status == 400 and self.reason == "BadDeviceToken"


def valid_token(value):
    return bool(TOKEN_PATTERN.fullmatch(value))


@lru_cache
def _load_key(pem):
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError):
        return None
    return key if isinstance(key, ec.EllipticCurvePrivateKey) else None


def _signing_key():
    settings = get_settings()
    pem = settings.apns_auth_key.get_secret_value().replace("\\n", "\n").strip()
    if not pem and settings.apns_auth_key_file:
        try:
            pem = Path(settings.apns_auth_key_file).read_text().strip()
        except OSError:
            return None
    return _load_key(pem) if pem else None


def configured():
    settings = get_settings()
    return bool(settings.apns_team_id and settings.apns_key_id and settings.apns_topic and _signing_key())


def provider_token():
    global _provider_token
    settings = get_settings()
    identity = (settings.apns_team_id, settings.apns_key_id)
    with _lock:
        token, issued, owner = _provider_token
        if token is None or owner != identity or time.time() - issued > TOKEN_SECONDS:
            issued = time.time()
            token = jwt.encode({"iss": settings.apns_team_id, "iat": int(issued)}, _signing_key(),
                               algorithm="ES256", headers={"kid": settings.apns_key_id})
            _provider_token = (token, issued, identity)
        return token


def client():
    global _client
    with _lock:
        if _client is None:
            # One persistent HTTP/2 connection per host, as Apple recommends.
            _client = httpx.Client(http2=True, timeout=10, follow_redirects=False)
        return _client


def send(device_token, environment, message):
    """Raises ApnsError for APNs rejections and httpx.HTTPError for transport failures."""
    settings = get_settings()
    alert = {"aps": {"alert": {"title": message["title"], "body": message["body"]},
                     "sound": "default", "thread-id": message.get("tag", "reminder")}}
    headers = {
        "authorization": f"bearer {provider_token()}",
        "apns-topic": settings.apns_topic,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-expiration": str(int(time.time()) + 24 * 60 * 60),
    }
    if message.get("tag"):
        headers["apns-collapse-id"] = message["tag"][:64]
    response = client().post(f"{HOSTS[environment]}/3/device/{device_token}", json=alert, headers=headers)
    if response.status_code != 200:
        try:
            reason = response.json().get("reason", "")
        except ValueError:
            reason = ""
        raise ApnsError(response.status_code, reason)
