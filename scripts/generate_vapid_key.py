"""Print a new VAPID private key for WEB_PUSH_VAPID_PRIVATE_KEY.

Replacing the key invalidates every browser subscription; landlords must turn
notifications on again.
"""

import base64

from cryptography.hazmat.primitives.asymmetric import ec


if __name__ == "__main__":
    private = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big")
    print(f"WEB_PUSH_VAPID_PRIVATE_KEY={base64.urlsafe_b64encode(private).decode().rstrip('=')}")
