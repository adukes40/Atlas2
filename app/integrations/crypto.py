"""Encrypts the credentials Atlas stores for other systems.

The key lives in .env (ATLAS_ENCRYPTION_KEY), not in the database, so a copy
of the database alone does not give anyone a working API token.
"""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class EncryptionNotConfigured(Exception):
    def __init__(self):
        super().__init__(
            "Atlas has no encryption key, so it cannot store credentials. "
            "Run sudo ./install.sh on the server again to create one."
        )


def _fernet():
    key = getattr(settings, "ATLAS_ENCRYPTION_KEY", "")
    if not key:
        raise EncryptionNotConfigured()
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise EncryptionNotConfigured() from None


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token):
    """The original text, or None if there is nothing stored or it cannot be
    read with the current key."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, EncryptionNotConfigured):
        return None
