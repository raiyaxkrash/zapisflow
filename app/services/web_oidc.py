"""Website Telegram OIDC; deliberately independent of Mini App initData.

Protocol: https://core.telegram.org/bots/telegram-login
Only RS256 is accepted. Configure the auth bot's default signing algorithm.
"""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ISSUER = "https://oauth.telegram.org"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_identity(token, keys, *, client_id, nonce, now=None):
    """Verify signature before reading any identity claim. Fail closed."""
    now = int(time.time()) if now is None else now
    try:
        if len(token) > 16384:
            raise ValueError
        header, payload, signature = token.split(".")
        meta = json.loads(decode(header))
        if meta.get("alg") != "RS256" or meta.get("crit"):
            raise ValueError
        candidates = [
            k
            for k in keys["keys"]
            if k.get("kid") == meta.get("kid")
            and k.get("kty") == "RSA"
            and k.get("use", "sig") == "sig"
            and k.get("alg", "RS256") == "RS256"
        ]
        if len(candidates) != 1:
            raise ValueError
        key = candidates[0]
        public = rsa.RSAPublicNumbers(
            int.from_bytes(decode(key["e"]), "big"),
            int.from_bytes(decode(key["n"]), "big"),
        ).public_key()
        public.verify(
            decode(signature),
            f"{header}.{payload}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        claims = json.loads(decode(payload))
        if (
            claims.get("iss") != ISSUER
            or claims.get("aud") != str(client_id)
            or type(claims.get("exp")) is not int
            or claims["exp"] <= now
            or type(claims.get("iat")) is not int
            or not now - 600 <= claims["iat"] <= now + 30
            or not isinstance(claims.get("nonce"), str)
            or not secrets.compare_digest(claims["nonce"], nonce)
            or type(claims.get("id")) is not int
            or not 0 < claims["id"] < 2**63
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"]
        ):
            raise ValueError
        return claims
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        InvalidSignature,
        OverflowError,
    ):
        # Cryptography/base64/JSON exceptions are all invalid untrusted input.
        # Never include the token or library exception detail in user/log output.
        raise ValueError("Invalid Telegram identity") from None


class TelegramOIDC:
    def __init__(self, redis, client_id, secret, redirect_uri, client=None):
        self.redis, self.client_id, self.secret, self.redirect_uri = (
            redis,
            client_id,
            secret,
            redirect_uri,
        )
        self.client = client

    async def begin(self, intent):
        state, binding, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(4))
        await self.redis.set(
            f"web:login:{digest(state)}",
            json.dumps(
                {
                    "binding": digest(binding),
                    "verifier": verifier,
                    "nonce": nonce,
                    "intent": intent,
                }
            ),
            ex=600,
            nx=True,
        )
        url = (
            ISSUER
            + "/auth?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "redirect_uri": self.redirect_uri,
                    "response_type": "code",
                    "scope": "openid profile phone",
                    "state": state,
                    "nonce": nonce,
                    "code_challenge_method": "S256",
                    "code_challenge": base64.urlsafe_b64encode(
                        hashlib.sha256(verifier.encode()).digest()
                    )
                    .decode()
                    .rstrip("="),
                }
            )
        )
        return url, binding

    async def complete(self, state, code, binding):
        if not state or len(state) > 128 or not code or len(code) > 4096 or not binding:
            raise ValueError("Login expired")
        # GETDEL consumes the attempt once across replicas, including failures.
        raw = await self.redis.getdel(f"web:login:{digest(state)}")
        if not raw:
            raise ValueError("Login expired")
        attempt = json.loads(raw)
        if not secrets.compare_digest(attempt["binding"], digest(binding)):
            raise ValueError("Login expired")
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as default:
            client = self.client or default
            response = await client.post(
                ISSUER + "/token",
                auth=(str(self.client_id), self.secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "code_verifier": attempt["verifier"],
                },
            )
            if response.status_code != 200:
                raise ValueError("Login failed")
            jwks = await client.get(ISSUER + "/.well-known/jwks.json")
            if jwks.status_code != 200:
                raise ValueError("Login failed")
            claims = validate_identity(
                response.json()["id_token"],
                jwks.json(),
                client_id=self.client_id,
                nonce=attempt["nonce"],
            )
        return claims, attempt["intent"]
