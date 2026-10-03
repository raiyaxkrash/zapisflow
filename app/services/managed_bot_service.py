"""Service for Telegram Managed Bot suggestion generation, username normalization, and deep links."""

from dataclasses import dataclass, field
import re
from typing import List, Optional
from urllib.parse import quote_plus

from aiogram.types import KeyboardButton, KeyboardButtonRequestManagedBot

TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

MAX_BOT_NAME_LENGTH = 64
MIN_USERNAME_LENGTH = 5
MAX_USERNAME_LENGTH = 32


@dataclass(frozen=True)
class BotSuggestions:
    """Prepared naming and username suggestions for a project."""

    suggested_name: str
    suggested_username: str
    alternative_usernames: List[str] = field(default_factory=list)
    base_slug: str = "master"


def transliterate_cyrillic(text: str) -> str:
    """Convert Cyrillic characters to Latin phonetic equivalents."""
    result = []
    for char in text.lower():
        result.append(TRANSLIT_MAP.get(char, char))
    return "".join(result)


def sanitize_base_slug(raw_name: str, fallback: str = "master") -> str:
    """Transliterate, strip unwanted chars, and produce a clean Latin slug."""
    translit = transliterate_cyrillic(raw_name)
    # Replace anything other than ASCII letters and digits with underscores
    slug = re.sub(r"[^a-z0-9]+", "_", translit)
    # Collapse multiple consecutive underscores
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or fallback


def normalize_bot_username(raw: str, fallback: str = "master") -> str:
    """Strictly normalize any username candidate to meet Telegram Bot API requirements:
    
    1. Only Latin letters (a-z), digits (0-9), and underscores.
    2. Must end with 'bot' (case-insensitive, normalized to lowercase).
    3. Length between 5 and 32 characters inclusive.
    4. No leading or trailing underscores before 'bot'.
    """
    clean = transliterate_cyrillic(raw)
    clean = re.sub(r"[^a-z0-9]+", "_", clean)
    clean = re.sub(r"_+", "_", clean).strip("_")
    if not clean:
        clean = fallback

    # Check if already ends with bot or _bot
    if clean.endswith("_bot"):
        base = clean[:-4].rstrip("_")
        suffix = "_bot"
    elif clean.endswith("bot") and len(clean) > 3:
        base = clean[:-3].rstrip("_")
        suffix = "bot"
    else:
        base = clean
        suffix = "_bot"

    if not base:
        base = fallback

    # Enforce maximum length of 32
    max_base_len = MAX_USERNAME_LENGTH - len(suffix)
    trimmed_base = base[:max_base_len].rstrip("_")
    if not trimmed_base:
        trimmed_base = "b"

    candidate = f"{trimmed_base}{suffix}"

    # Enforce minimum length of 5
    if len(candidate) < MIN_USERNAME_LENGTH:
        needed = MIN_USERNAME_LENGTH - len(candidate)
        candidate = f"{trimmed_base}{'x' * needed}{suffix}"

    return candidate[:MAX_USERNAME_LENGTH]


def generate_bot_suggestions(project_name: str) -> BotSuggestions:
    """Generate primary and alternative bot name and username suggestions based on project name."""
    clean_name = project_name.strip() if project_name else "Мастер"
    suggested_name = clean_name[:MAX_BOT_NAME_LENGTH]

    base_slug = sanitize_base_slug(clean_name, fallback="master")

    # 1. Primary suggestion: {base}_bot (or {base}bot if base ends nicely)
    primary = normalize_bot_username(f"{base_slug}_bot")

    # 2. Alternative suggestions
    alternatives: List[str] = []

    alt1 = normalize_bot_username(f"{base_slug}_zapis_bot")
    if alt1 != primary and alt1 not in alternatives:
        alternatives.append(alt1)

    alt2 = normalize_bot_username(f"{base_slug}_booking_bot")
    if alt2 != primary and alt2 not in alternatives:
        alternatives.append(alt2)

    alt3 = normalize_bot_username(f"zapis_{base_slug}_bot")
    if alt3 != primary and alt3 not in alternatives:
        alternatives.append(alt3)

    return BotSuggestions(
        suggested_name=suggested_name,
        suggested_username=primary,
        alternative_usernames=alternatives,
        base_slug=base_slug,
    )


def build_managed_bot_deep_link(
    manager_bot_username: str,
    suggested_username: str,
    suggested_name: str,
) -> str:
    """Build official Bot API 9.6 Managed Bot creation deep link.
    
    Format: https://t.me/newbot/{manager_bot_username}/{suggested_bot_username}?name={suggested_bot_name}
    """
    clean_manager = manager_bot_username.lstrip("@").strip()
    clean_suggested_username = suggested_username.lstrip("@").strip()
    encoded_name = quote_plus(suggested_name[:MAX_BOT_NAME_LENGTH])
    return f"https://t.me/newbot/{clean_manager}/{clean_suggested_username}?name={encoded_name}"


def build_managed_bot_request_button(
    suggested_name: str,
    suggested_username: str,
    request_id: int,
    button_text: str = "🤖 Нажмите, чтобы создать бота",
) -> KeyboardButton:
    """Construct official KeyboardButtonRequestManagedBot button for reply keyboards."""
    return KeyboardButton(
        text=button_text,
        request_managed_bot=KeyboardButtonRequestManagedBot(
            request_id=request_id,
            suggested_name=suggested_name[:MAX_BOT_NAME_LENGTH],
            suggested_username=suggested_username.lstrip("@")[:MAX_USERNAME_LENGTH],
        ),
    )


class ManagedBotService:
    """Service providing suggestion generation, validation, and deep links for Managed Bots."""

    @staticmethod
    def transliterate_cyrillic(text: str) -> str:
        return transliterate_cyrillic(text)

    @staticmethod
    def normalize_username(raw: str) -> tuple[Optional[str], Optional[str]]:
        """Validate and normalize candidate username.
        
        Returns:
            (normalized_username, error_message or None)
        """
        clean = transliterate_cyrillic(raw).strip().lower()
        clean = re.sub(r"\s+", "_", clean)
        
        # Check allowed chars
        if not re.match(r"^[a-z0-9_]+$", clean):
            return None, "Логин может содержать только латинские буквы, цифры и символ подчёркивания."
            
        if not (clean.endswith("bot") or clean.endswith("_bot")):
            clean = f"{clean}_bot"

        if len(clean) < MIN_USERNAME_LENGTH or len(clean) > MAX_USERNAME_LENGTH:
            return None, f"Длина логина должна быть от {MIN_USERNAME_LENGTH} до {MAX_USERNAME_LENGTH} символов."

        return clean, None

    @staticmethod
    def suggest_username_and_name(project_name: str) -> tuple[str, str]:
        suggestions = generate_bot_suggestions(project_name)
        display_name = f"{suggestions.suggested_name} | Запись" if "запись" not in suggestions.suggested_name.lower() else suggestions.suggested_name
        return suggestions.suggested_username, display_name[:MAX_BOT_NAME_LENGTH]

    @staticmethod
    def generate_username_variants(raw: str) -> list[str]:
        slug = sanitize_base_slug(raw)
        return [
            normalize_bot_username(f"{slug}_bot"),
            normalize_bot_username(f"{slug}_zapis_bot"),
            normalize_bot_username(f"{slug}_booking_bot"),
            normalize_bot_username(f"zapis_{slug}_bot"),
        ]

    @staticmethod
    def build_managed_bot_deep_link(
        manager_bot_username: str,
        suggested_username: str,
        suggested_name: str,
    ) -> str:
        return build_managed_bot_deep_link(manager_bot_username, suggested_username, suggested_name)

    @staticmethod
    def build_request_managed_bot_button(
        text: str,
        suggested_name: str,
        suggested_username: str,
        request_id: int = 1,
    ) -> KeyboardButton:
        return build_managed_bot_request_button(
            suggested_name=suggested_name,
            suggested_username=suggested_username,
            request_id=request_id,
            button_text=text,
        )

