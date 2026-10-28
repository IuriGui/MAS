import pytest

import pipeline


@pytest.fixture(autouse=True)
def sem_build(monkeypatch):
    """A etapa Build chama mvn/npm de verdade: fica desligada, exceto nos testes
    que a ligam (e trocam o compilador por um falso)."""
    monkeypatch.setattr(pipeline, "BUILD", False)
