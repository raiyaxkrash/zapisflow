import base64
import json
from urllib.parse import parse_qs, urlsplit

import fakeredis.aioredis
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.web_oidc import ISSUER, TelegramOIDC, validate_identity


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@pytest.fixture
def signing():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kid": "test",
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "n": encode(numbers.n.to_bytes(256, "big")),
                "e": encode(numbers.e.to_bytes(3, "big")),
            }
        ]
    }

    def token(**changes):
        claims = {
            "iss": ISSUER,
            "aud": "123",
            "sub": "opaque-oidc-subject",
            "id": 456,
            "exp": 1300,
            "iat": 1000,
            "nonce": "nonce",
            **changes,
        }
        body = (
            encode(json.dumps({"alg": "RS256", "kid": "test"}).encode())
            + "."
            + encode(json.dumps(claims).encode())
        )
        return (
            body
            + "."
            + encode(key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256()))
        )

    return token, jwks


def test_verified_identity_uses_id_not_subject(signing):
    token, keys = signing
    assert (
        validate_identity(token(), keys, client_id="123", nonce="nonce", now=1001)["id"]
        == 456
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://evil.test"},
        {"aud": "other"},
        {"exp": 1000},
        {"iat": 1400},
        {"iat": 0},
        {"nonce": "other"},
        {"id": "456"},
        {"id": -1},
        {"sub": ""},
    ],
)
def test_invalid_claims(signing, changes):
    token, keys = signing
    with pytest.raises(ValueError, match="Invalid Telegram identity"):
        validate_identity(
            token(**changes), keys, client_id="123", nonce="nonce", now=1001
        )


def test_forged_signature(signing):
    token, keys = signing
    parts = token().split(".")
    parts[1] = encode(json.dumps({"id": 999}).encode())
    with pytest.raises(ValueError):
        validate_identity(
            ".".join(parts), keys, client_id="123", nonce="nonce", now=1001
        )


@pytest.mark.asyncio
async def test_pkce_nonce_state_binding_and_replay():
    redis = fakeredis.aioredis.FakeRedis()
    client = TelegramOIDC(
        redis,
        "123",
        "not-a-real-secret",
        "https://site.test/api/auth/telegram/callback",
    )
    url, binding = await client.begin({"bot_public_id": "test-intent"})
    params = parse_qs(urlsplit(url).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["response_type"] == ["code"]
    assert params["nonce"] and params["state"]
    assert "not-a-real-secret" not in url
    state = params["state"][0]
    with pytest.raises(ValueError, match="Login expired"):
        await client.complete(state, "code", "different-browser")
    with pytest.raises(ValueError, match="Login expired"):
        await client.complete(state, "code", binding)
    await redis.aclose()
