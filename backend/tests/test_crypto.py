"""核心加密：Fernet 对称加解密。"""
from app.core.crypto import decrypt, encrypt


def test_round_trip():
    plain = "sk-abcdef-1234567890"
    cipher = encrypt(plain)
    assert cipher and cipher != plain
    assert decrypt(cipher) == plain


def test_empty_string_passthrough():
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_decrypt_garbage_returns_empty():
    """解密失败时返回空字符串，不应抛异常。"""
    assert decrypt("not-a-valid-fernet-token") == ""


def test_encrypted_value_changes_each_call():
    """Fernet 含随机 IV，相同明文每次密文不同（但都能解回）。"""
    plain = "same-input"
    a = encrypt(plain)
    b = encrypt(plain)
    assert a != b
    assert decrypt(a) == plain
    assert decrypt(b) == plain
