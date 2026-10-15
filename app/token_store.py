"""Store the Bitbucket token in the current user's native credential vault."""

import importlib
import sys


# Keep these identifiers stable across updates and executable locations.
SERVICE_NAME = "BitbucketPRManager"
ACCOUNT_NAME = "bitbucket-cloud-token"


class TokenStoreError(Exception):
    """A credential-store failure that is safe to show without backend details."""


def _get_backend():
    """Select a native backend explicitly, without plugins or plaintext fallbacks."""
    if sys.platform == "darwin":
        module_name, class_name = "keyring.backends.macOS", "Keyring"
    elif sys.platform == "win32":
        module_name, class_name = "keyring.backends.Windows", "WinVaultKeyring"
    elif sys.platform.startswith("linux"):
        module_name, class_name = "keyring.backends.SecretService", "Keyring"
    else:
        raise TokenStoreError("Системное хранилище токенов недоступно. Токен можно ввести вручную.")

    try:
        module = importlib.import_module(module_name)
        return getattr(module, class_name)()
    except Exception:
        # Backend errors may contain credentials or environment details.
        raise TokenStoreError(
            "Системное хранилище токенов недоступно. Токен можно ввести вручную."
        ) from None


def load_token() -> str:
    """Return the saved token, or an empty string if no token has been saved."""
    backend = _get_backend()
    try:
        token = backend.get_password(SERVICE_NAME, ACCOUNT_NAME)
        if token is None:
            return ""
        if not isinstance(token, str):
            raise TypeError
        return token.strip()
    except Exception:
        raise TokenStoreError("Не удалось прочитать токен из системного хранилища.") from None


def save_token(token: str) -> None:
    """Save a nonempty token; an empty input never overwrites an existing token."""
    if not isinstance(token, str) or not token.strip():
        raise TokenStoreError("Введите токен перед сохранением.")
    backend = _get_backend()
    try:
        backend.set_password(SERVICE_NAME, ACCOUNT_NAME, token.strip())
    except Exception:
        raise TokenStoreError("Не удалось сохранить токен в системном хранилище.") from None


def delete_token() -> None:
    """Delete the saved token, reporting denied or failed deletions to the caller."""
    backend = _get_backend()
    try:
        # Some backends use PasswordDeleteError both for missing entries and for
        # denied access. Only skip deletion when the preceding read finds none.
        if backend.get_password(SERVICE_NAME, ACCOUNT_NAME) is None:
            return
        backend.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise TokenStoreError("Не удалось удалить токен из системного хранилища.") from None
