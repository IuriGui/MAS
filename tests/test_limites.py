"""Garantias de que nenhuma repetição é infinita — sem chamar a API do Gemini.

Usa o create_agent real do LangChain com modelos falsos, porque o retry
problemático (saída estruturada inválida -> nova chamada, até 9999 passos) é
interno da biblioteca.
"""
import importlib

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import config
import implementacao
from config import LIMITE_PASSOS_AGENTE
from execucao import PipelineError, executar_agente
from schemas import ModuleAnalysis
from tests.fakes import arq
from tests.test_pipeline import AgenteFalso, spec

VALIDA = {"module_name": "U", "scope_summary": "s", "problems": [],
          "functional_requirements": [], "non_functional_requirements": [],
          "dependencies": [], "observations": []}


class ModeloFalso(BaseChatModel):
    """Erra o formato da saída estruturada nas `erros` primeiras chamadas."""
    erros: int = 10**6
    chamadas: int = 0
    ferramenta: str = ""

    @property
    def _llm_type(self):
        return "falso"

    def bind_tools(self, tools, **kw):
        t = tools[0]
        self.ferramenta = t.name if hasattr(t, "name") else t["function"]["name"]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.chamadas += 1
        if self.chamadas > 100:  # rede de proteção do próprio teste
            raise RuntimeError("laço sem fim")
        args = {"lixo": 1} if self.chamadas <= self.erros else VALIDA
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content="", tool_calls=[{"name": self.ferramenta, "args": args,
                                     "id": str(self.chamadas)}]))])


def agente(modelo):
    return create_agent(model=modelo, response_format=ModuleAnalysis)


def test_formato_sempre_errado_para_no_limite():
    m = ModeloFalso()
    with pytest.raises(PipelineError, match="saída estruturada"):
        executar_agente("t", agente(m), "x", lambda o: [], {}, max_tentativas=2)
    assert m.chamadas == 2 * LIMITE_PASSOS_AGENTE


def test_formato_corrigido_dentro_da_tentativa():
    m = ModeloFalso(erros=1)
    obj, erros = executar_agente("t", agente(m), "x", lambda o: [], {}, max_tentativas=2)
    assert erros == [] and obj.module_name == "U"
    assert m.chamadas == 2


def test_formato_errado_depois_certo_na_tentativa_seguinte():
    m = ModeloFalso(erros=LIMITE_PASSOS_AGENTE)  # esgota a 1ª tentativa
    metricas = {}
    obj, erros = executar_agente("t", agente(m), "x", lambda o: [], metricas, 2)
    assert erros == [] and metricas["t"]["tentativas"] == 2


def test_validacao_sempre_reprovada_para_em_max_tentativas():
    falso = AgenteFalso(spec())
    obj, erros = executar_agente("t", falso, "x", lambda o: ["sempre ruim"], {}, 3)
    assert erros == ["sempre ruim"] and len(falso.chamadas) == 3


def test_resposta_sem_structured_response_nao_quebra():
    class SemSaida:
        n = 0
        def invoke(self, entrada, config=None):
            self.n += 1
            return {"messages": []}
    a = SemSaida()
    with pytest.raises(PipelineError):
        executar_agente("t", a, "x", lambda o: [], {}, 2)
    assert a.n == 2


def test_limite_de_passos_vai_na_config():
    falso = AgenteFalso(spec())
    executar_agente("t", falso, "x", lambda o: [], {}, 1)
    assert falso.configs[0]["recursion_limit"] == LIMITE_PASSOS_AGENTE


def test_lotes_de_tamanho_zero_terminam():
    arquivos = [arq(f"a{i}.ts", "other") for i in range(3)]
    lotes = implementacao.montar_lotes(arquivos, 0)
    assert [len(l) for l in lotes] == [1, 1, 1]


def test_config_nao_aceita_zero(monkeypatch):
    monkeypatch.setenv("MAX_TENTATIVAS", "0")
    monkeypatch.setenv("IMPL_LOTE", "0")
    monkeypatch.setenv("LIMITE_PASSOS_AGENTE", "0")
    try:
        c = importlib.reload(config)
        assert c.MAX_TENTATIVAS == 1 and c.IMPL_LOTE == 1 and c.LIMITE_PASSOS_AGENTE == 2
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_modelo_criado_com_retry_http_e_timeout_limitados(monkeypatch):
    import agents._base as base
    capturado = {}

    def falso_init(modelo, **kw):
        capturado.update(kw)
        return ModeloFalso()
    monkeypatch.setattr(base, "init_chat_model", falso_init)
    base.criar_agente_estruturado("x", ModuleAnalysis)
    assert capturado["max_retries"] == config.MAX_RETRIES_API
    assert capturado["timeout"] == config.TIMEOUT_API > 0


# --------------------------------------------------------- API indisponível
class ErroAPI(Exception):
    pass


class AgenteInstavel:
    """Falha com erro transitório nas `falhas` primeiras chamadas."""

    def __init__(self, falhas, erro=None):
        self.falhas, self.chamadas = falhas, 0
        self.erro = erro or ErroAPI("503 UNAVAILABLE. This model is experiencing high demand.")

    def invoke(self, entrada, config=None):
        self.chamadas += 1
        if self.chamadas <= self.falhas:
            raise self.erro
        return {"structured_response": spec(), "messages": []}


def test_api_indisponivel_espera_e_chama_de_novo(monkeypatch):
    import execucao
    esperas = []
    monkeypatch.setattr(execucao, "ESPERAS_API", (1, 2, 3))
    monkeypatch.setattr(execucao.time, "sleep", esperas.append)
    m = {}
    obj, erros = execucao.executar_agente("t", AgenteInstavel(2), "x", lambda o: [], m, 1)
    assert erros == [] and esperas == [1, 2]
    assert m["t"]["tentativas"] == 1  # esperar a API não gasta tentativa
    assert m["t"]["erros_api"] == 2 and m["t"]["espera_api_s"] == 3


def test_api_indisponivel_alem_das_esperas_propaga(monkeypatch):
    import execucao
    monkeypatch.setattr(execucao, "ESPERAS_API", (1,))
    monkeypatch.setattr(execucao.time, "sleep", lambda s: None)
    with pytest.raises(ErroAPI):
        execucao.executar_agente("t", AgenteInstavel(5), "x", lambda o: [], {}, 1)


def test_erro_que_nao_e_transitorio_propaga_na_hora(monkeypatch):
    import execucao
    monkeypatch.setattr(execucao.time, "sleep", lambda s: pytest.fail("não devia esperar"))
    agente = AgenteInstavel(1, erro=ValueError("chave de API inválida"))
    with pytest.raises(ValueError):
        execucao.executar_agente("t", agente, "x", lambda o: [], {}, 1)
    assert agente.chamadas == 1


@pytest.mark.parametrize("erro", [
    "httpx.RemoteProtocolError: Server disconnected without sending a response.",
    "Connection reset by peer",
])
def test_queda_de_conexao_e_transitoria(erro, monkeypatch):
    import execucao
    monkeypatch.setattr(execucao, "ESPERAS_API", (1,))
    monkeypatch.setattr(execucao.time, "sleep", lambda s: None)
    obj, erros = execucao.executar_agente("t", AgenteInstavel(1, erro=ErroAPI(erro)), "x",
                                          lambda o: [], {}, 1)
    assert erros == []


def test_queda_de_rede_reconhecida_pela_classe_na_cadeia():
    import execucao

    class RemoteProtocolError(Exception):
        pass

    try:
        try:
            raise RemoteProtocolError("")
        except RemoteProtocolError as e:
            raise RuntimeError("falha no modelo") from e
    except RuntimeError as externo:
        assert execucao.erro_transitorio(externo)
    assert not execucao.erro_transitorio(ValueError("chave de API inválida"))
