"""Authenticated token encryption service using AES-256-GCM.

Provides reversible, authenticated, non-deterministic encryption for Telegram bot tokens.
Format: v1:<key_id>:<base64(nonce + ciphertext + tag)>
Tied to specific BotInstance via Associated Authenticated Data (AAD).
"""

import base64
import os
import re
from typing import Optional, Union

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config.settings import settings
from app.services.exceptions import TokenCryptoConfigError, TokenDecryptionError


def _normalize_aad(aad: Union[str, bytes, int, None]) -> bytes:
    """Normalize Associated Authenticated Data (AAD) into bytes."""
    if aad is None:
        return b""
    if isinstance(aad, bytes):
        return aad
    if isinstance(aad, int):
        return f"bot:{aad}".encode("utf-8")
    return str(aad).encode("utf-8")


class TokenCrypto:
    """AES-256-GCM authenticated cipher for Telegram bot tokens."""

    CURRENT_VERSION = "v1"
    DEFAULT_KEY_ID = "k1"
    NONCE_LENGTH = 12  # 96 bits recommended for AES-GCM
    KEY_LENGTH = 32    # 256 bits

    def __init__(
        self,
        master_key: Optional[Union[str, bytes]] = None,
        key_id: str = DEFAULT_KEY_ID,
    ) -> None:
        self.key_id = key_id
        raw_key = master_key if master_key is not None else settings.bot_token_encryption_key
        self._key_bytes = self._parse_key(raw_key)
        self._aesgcm = AESGCM(self._key_bytes)

    @classmethod
    def _parse_key(cls, key: Union[str, bytes]) -> bytes:
        """Parse and validate 256-bit (32 bytes) master encryption key.

        Supports:
        - raw 32 bytes
        - 64 hex characters
        - 32-byte URL-safe or standard Base64 string
        """
        if not key:
            raise TokenCryptoConfigError(
                "BOT_TOKEN_ENCRYPTION_KEY is empty or not configured"
            )

        if isinstance(key, str):
            key_str = key.strip()
            if (
                key_str.startswith("CHANGE_ME")
                or key_str == "0" * 64
                or "placeholder" in key_str.lower()
                or key_str.lower() in {
                    "change_me_generate_32_byte_hex_key",
                    "change_me_generate_32_byte_key",
                }
            ):
                raise TokenCryptoConfigError(
                    "BOT_TOKEN_ENCRYPTION_KEY is configured with an insecure placeholder value. "
                    "Generate a secure key using: python -c 'import secrets; print(secrets.token_hex(32))'"
                )

        if isinstance(key, bytes):
            if len(key) != cls.KEY_LENGTH or key == b"\x00" * cls.KEY_LENGTH:
                raise TokenCryptoConfigError(
                    f"Master encryption key must be {cls.KEY_LENGTH} non-zero bytes (got {len(key)})"
                )
            return key

        key_str = key.strip()

        # 1. Try 64-char hex
        if len(key_str) == 64 and re.fullmatch(r"[0-9a-fA-F]+", key_str):
            try:
                parsed = bytes.fromhex(key_str)
                if len(parsed) == cls.KEY_LENGTH:
                    return parsed
            except ValueError:
                pass

        # 2. Try Base64 / URL-safe Base64
        try:
            parsed = base64.b64decode(key_str)
            if len(parsed) == cls.KEY_LENGTH:
                return parsed
        except Exception:
            try:
                parsed = base64.urlsafe_b64decode(key_str)
                if len(parsed) == cls.KEY_LENGTH:
                    return parsed
            except Exception:
                pass

        # 3. Direct UTF-8 bytes check
        raw_bytes = key_str.encode("utf-8")
        if len(raw_bytes) == cls.KEY_LENGTH:
            return raw_bytes

        raise TokenCryptoConfigError(
            f"Invalid master encryption key format: expected 32 bytes (256 bits), "
            f"64 hex characters, or 44-character Base64. Got {len(key_str)} characters."
        )

    def encrypt(
        self,
        token: str,
        associated_data: Union[str, bytes, int, None] = None,
    ) -> str:
        """Encrypt plaintext bot token with AES-256-GCM.

        Returns versioned ciphertext string: v1:<key_id>:<base64(nonce + ct + tag)>
        """
        if not token:
            raise ValueError("Token to encrypt cannot be empty")

        token_bytes = token.encode("utf-8")
        nonce = os.urandom(self.NONCE_LENGTH)
        aad_bytes = _normalize_aad(associated_data)

        ciphertext = self._aesgcm.encrypt(nonce, token_bytes, aad_bytes)
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

        return f"{self.CURRENT_VERSION}:{self.key_id}:{payload}"

    def decrypt(
        self,
        encrypted_token: str,
        associated_data: Union[str, bytes, int, None] = None,
    ) -> str:
        """Decrypt versioned ciphertext and verify authentication tag and AAD.

        Raises TokenDecryptionError if corrupted, tampered, wrong key or wrong AAD.
        """
        if not encrypted_token or not isinstance(encrypted_token, str):
            raise TokenDecryptionError("Encrypted token must be a non-empty string")

        parts = encrypted_token.split(":")
        if len(parts) != 3:
            raise TokenDecryptionError(
                "Invalid encrypted token format: expected version:key_id:payload"
            )

        version, key_id, payload_b64 = parts
        if version != self.CURRENT_VERSION:
            raise TokenDecryptionError(
                f"Unsupported ciphertext version '{version}', expected '{self.CURRENT_VERSION}'"
            )

        try:
            data = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        except Exception as e:
            raise TokenDecryptionError(
                "Corrupted base64 payload in encrypted token"
            ) from e

        if len(data) < self.NONCE_LENGTH + 16:  # 12 nonce + 16 tag minimum
            raise TokenDecryptionError("Ciphertext payload is truncated")

        nonce = data[: self.NONCE_LENGTH]
        ciphertext = data[self.NONCE_LENGTH :]
        aad_bytes = _normalize_aad(associated_data)

        try:
            decrypted_bytes = self._aesgcm.decrypt(nonce, ciphertext, aad_bytes)
            return decrypted_bytes.decode("utf-8")
        except InvalidTag as e:
            raise TokenDecryptionError(
                "Failed to decrypt token: authentication tag verification failed "
                "(wrong key, corrupted data, or mismatched associated data)"
            ) from e
        except UnicodeDecodeError as e:
            raise TokenDecryptionError("Decrypted token is not valid UTF-8") from e

    @staticmethod
    def generate_key() -> str:
        """Generate a random 32-byte (256-bit) encryption key as a 64-character hex string."""
        return os.urandom(TokenCrypto.KEY_LENGTH).hex()

    def __repr__(self) -> str:
        return f"<TokenCrypto key_id='{self.key_id}' configured=True>"
