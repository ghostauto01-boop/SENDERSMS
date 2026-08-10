"""
Tests for authentication and security.
"""

import pytest
from app.security.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    generate_csrf_token,
    verify_csrf_token,
)
from app.security.encryption import encrypt_value, decrypt_value, mask_value


class TestPasswordHashing:
    def test_hash_password_returns_string(self):
        hashed = hash_password("mypassword")
        assert isinstance(hashed, str)
        assert hashed.startswith("$argon2id")

    def test_verify_correct_password(self):
        hashed = hash_password("correct")
        assert verify_password("correct", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_different_passwords_get_different_hashes(self):
        h1 = hash_password("password1")
        h2 = hash_password("password2")
        assert h1 != h2

    def test_same_password_different_salts(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # Different salts
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True


class TestJWT:
    def test_create_and_decode_token(self):
        token = create_access_token({"sub": "1", "role": "admin"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "1"
        assert payload["role"] == "admin"

    def test_decode_invalid_token(self):
        assert decode_access_token("invalid.token.here") is None
        assert decode_access_token("") is None

    def test_token_contains_expiry(self):
        token = create_access_token({"sub": "1"})
        payload = decode_access_token(token)
        assert "exp" in payload


class TestCSRF:
    def test_generate_and_verify(self):
        token = generate_csrf_token("my-secret")
        assert verify_csrf_token(token, "my-secret") is True

    def test_wrong_secret(self):
        token = generate_csrf_token("correct-secret")
        assert verify_csrf_token(token, "wrong-secret") is False

    def test_invalid_token_format(self):
        assert verify_csrf_token("not-valid", "secret") is False


class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        original = "my-secret-password-123"
        encrypted = encrypt_value(original)
        assert encrypted != original
        decrypted = decrypt_value(encrypted)
        assert decrypted == original

    def test_encrypt_empty_string(self):
        assert encrypt_value("") == ""

    def test_decrypt_empty_string(self):
        assert decrypt_value("") == ""

    def test_decrypt_invalid_data(self):
        assert decrypt_value("not-valid-encrypted-data") == ""

    def test_mask_value(self):
        assert mask_value("secret123", 3) == "******123"
        assert mask_value("ab", 3) == "**"
        assert mask_value("", 4) == ""
        assert mask_value("password1234", 4) == "********1234"
