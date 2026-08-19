import os

import pytest


@pytest.fixture(autouse=True)
def isolated_env():
    """Возвращает os.environ в исходное состояние после каждого теста.

    load_dotenv() пишет в os.environ напрямую, мимо monkeypatch, поэтому без
    восстановления переменные протекали бы между тестами.
    """
    saved = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(saved)
