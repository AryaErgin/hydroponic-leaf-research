"""Explicit secret sources only. No discovery, logging, or cloud mutations."""
from pathlib import Path


def secret_manager_value(resource_name):
    if not resource_name.startswith('projects/') or '/secrets/' not in resource_name or '/versions/' not in resource_name:
        raise ValueError('Use an explicit Secret Manager secret version resource name')
    try:
        from google.cloud import secretmanager
    except ImportError:
        raise ValueError('Optional google-cloud-secret-manager package is required for this configured secret source') from None
    try:
        response=secretmanager.SecretManagerServiceClient().access_secret_version(request={'name':resource_name})
        return response.payload.data.decode('utf-8').strip()
    except Exception:
        raise ValueError('Configured Secret Manager version could not be read. Check access and version state.') from None


def local_hmac_key(path):
    """Read an operator-protected file. Windows ACL protection is an operator step."""
    target=Path(path)
    if target.is_symlink():raise ValueError('HMAC key must be a regular protected file')
    import os
    if os.name!='nt' and target.stat().st_mode & 0o077:
        raise ValueError('HMAC key must be readable only by its owner')
    value=target.read_bytes()
    if len(value)<32:raise ValueError('Email HMAC key must contain at least 32 random bytes')
    return value
