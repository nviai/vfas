import pytest

from app.features.ai import client as ai_client
from tests.ai.fake_llm import FakeLLM


@pytest.fixture()
def fake_llm():
    fake = FakeLLM().install()
    yield fake
    ai_client.set_transport(None)
