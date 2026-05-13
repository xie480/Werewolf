from cryptography.fernet import Fernet
from ai_werewolf_core.config import settings
import base64
import hashlib

def get_fernet() -> Fernet:
    # 确保密钥是 32 字节的 url-safe base64 编码
    key = settings.crypto_key.encode('utf-8')
    # 如果不是 32 字节，则使用 sha256 哈希并进行 base64 编码
    if len(key) != 44 or not key.endswith(b'='):
        key = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
    return Fernet(key)

def encrypt_api_key(api_key: str) -> str:
    if not api_key:
        return api_key
    f = get_fernet()
    return f.encrypt(api_key.encode('utf-8')).decode('utf-8')

def decrypt_api_key(encrypted_key: str) -> str:
    if not encrypted_key:
        return encrypted_key
    f = get_fernet()
    try:
        return f.decrypt(encrypted_key.encode('utf-8')).decode('utf-8')
    except Exception:
        # 如果未加密或解密失败，则回退返回原值
        return encrypted_key
