"""Select alias storage without silently falling back after API failures."""

from .repository import RepositoryUnavailableError


def repository_settings(secret):
    url, token = secret("ALIAS_API_URL"), secret("ALIAS_API_TOKEN")
    if url or token:
        if not url or not token:
            raise RepositoryUnavailableError("Configure both ALIAS_API_URL and ALIAS_API_TOKEN")
        return "api", url, token
    url, key = secret("SUPABASE_URL"), secret("SUPABASE_SERVICE_KEY")
    return ("supabase", url, key) if url and key else None
