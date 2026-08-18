import pytest


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Settings/provider factories are @lru_cache'd for prod use; tests that patch
    env vars need a clean slate before and after."""
    from app.core.config import get_settings
    from app.embeddings.factory import get_embedding_provider
    from app.llm.factory import get_llm_provider

    get_settings.cache_clear()
    get_llm_provider.cache_clear()
    get_embedding_provider.cache_clear()
    yield
    get_settings.cache_clear()
    get_llm_provider.cache_clear()
    get_embedding_provider.cache_clear()
