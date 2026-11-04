"""OpenSSL-backed scrypt; OWASP minimum when Argon2id is unavailable.

N=2**17,r=8,p=1. Salt per credential; maximum input length bounds hashing cost.
No passwords or raw recovery/session tokens are written to logs.
"""
import hashlib
import hmac
import secrets


def validate_password(password):
    if not 15<=len(password)<=128:
        raise ValueError('Use a password or passphrase of 15–128 characters')


def derive(password,salt):
    return hashlib.scrypt(password.encode('utf-8'),salt=bytes.fromhex(salt),n=131072,r=8,p=1,
                          maxmem=256*1024*1024,dklen=32).hex()


def hash_password(password):
    validate_password(password)
    salt=secrets.token_hex(16)
    return f'scrypt$131072$8$1${salt}${derive(password,salt)}'


def verify_password(password,encoded):
    if not isinstance(password,str) or len(password)>128:
        return False
    try:
        algorithm,n,r,p,salt,digest=encoded.split('$')
        if (algorithm,n,r,p)!=('scrypt','131072','8','1'):
            return False
        return hmac.compare_digest(derive(password,salt),digest)
    except (ValueError,TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()
