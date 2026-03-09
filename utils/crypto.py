import base64
import hashlib

from cryptography.fernet import Fernet


def get_fernet(secret_key: str) -> Fernet:
    """Deriva clave Fernet de 32 bytes desde el SECRET_KEY."""
    key = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(text: str, secret_key: str) -> str:
    """Encripta texto con Fernet derivado del secret_key."""
    return get_fernet(secret_key).encrypt(text.encode()).decode()


def decrypt(token: str, secret_key: str) -> str:
    """Desencripta token con Fernet derivado del secret_key."""
    return get_fernet(secret_key).decrypt(token.encode()).decode()
