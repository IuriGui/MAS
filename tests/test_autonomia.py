"""Autonomia (F2): tools de leitura do ambiente e submissão validada no loop do agente.

Usa o create_agent real do LangChain com um modelo falso roteirizado, sem chamar
a API do Gemini.
"""
import json
from typing import List

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import agents._base as base
from agents import ferramentas
from ambiente import Ambiente
from execucao import executar_agente
from schemas import ModuleAnalysis
from tests.test_pipeline import arquitetura
from tests.test_projeto import contexto

VALIDA = {"module_name": "Users", "scope_summary": "ok", "problems": [],
          "functional_requirements": [], "non_functional_requirements": [],
          "dependencies": [], "observations": []}
RUIM = {**VALIDA, "scope_summary": "ruim"}


def valida_escopo(obj) -> List[str]:
    return ["Duplicate requirement IDs: ['FR01']."] if obj.scope_summary == "ruim" else []


class ModeloRoteiro(BaseChatModel):
    """Responde, em ordem, com as tool calls do roteiro (repete a última)."""
    roteiro: list
    recebidas: list = []
    ferramentas_vistas: list = []

    @property
    def _llm_type(self):
        return "roteiro"

    def bind_tools(self, tools, **kw):
        self.ferramentas_vistas = [getattr(t, "name", None) or t["function"]["name"]
                                   for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.recebidas.append(list(messages))
        if len(self.recebidas) > 50:
            raise RuntimeError("laço sem fim")
        nome, args = self.roteiro[min(len(self.recebidas), len(self.roteiro)) - 1]
        msg = AIMessage(content="", tool_calls=[
            {"name": nome, "args": args, "id": f"c{len(self.recebidas)}"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _agente(modelo, tools=ferramentas.ESPECIFICACAO):
    return base.criar_agente_estruturado("Role: test.", ModuleAnalysis, tools,
                                         modelo=modelo, autonomia=True)


def _ambiente(tmp_path):
    amb = Ambiente(tmp_path, contexto())
    amb.publicar("Catalog", "arquiteto_software", arquitetura(caminho="/api/v1/products"))
    return amb


def _tool_msgs(mensagens):
    return [m for m in mensagens if isinstance(m, ToolMessage)]


# --------------------------------------------------------- loop do agente
def test_le_o_ambiente_corrige_e_entrega(tmp_path):
    amb = _ambiente(tmp_path)
    modelo = ModeloRoteiro(roteiro=[
        ("get_artifact", {"module": "Catalog", "artifact": "architecture"}),
        ("ModuleAnalysis", RUIM),
        ("ModuleAnalysis", VALIDA),
    ])
    m = {}
    obj, erros = executar_agente("requisitos", _agente(modelo), "x", valida_escopo, m, 2,
                                 modulo="Users", leitura=amb.leitura())

    assert erros == [] and type(obj) is ModuleAnalysis and obj.scope_summary == "ok"
    assert {"get_artifact", "get_contracts", "ModuleAnalysis"} <= set(modelo.ferramentas_vistas)
    # 2ª chamada: viu a arquitetura do Catalog lida do ambiente
    lida = _tool_msgs(modelo.recebidas[1])[-1].content
    assert json.loads(lida)["endpoints"][0]["path"] == "/api/v1/products"
    # 3ª chamada: viu a reprovação com os erros da validação
    reprovacao = _tool_msgs(modelo.recebidas[2])[-1].content
    assert "REJECTED" in reprovacao and "Duplicate requirement IDs" in reprovacao

    r = m["requisitos"]
    assert r["tentativas"] == 1 and r["reprovadas"] == 1 and r["sem_saida"] == 0
    assert r["chamadas_tool"] == 1 and r["tools_por_nome"] == {"get_artifact": 1}
    assert r["chamadas_modelo"] == 3
    assert r["erros_por_regra"] == {"Duplicate requirement IDs: [_].": 1}


def test_orcamento_esgotado_reexecuta_com_os_ultimos_erros(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "LIMITE_CHAMADAS_AGENTE", 3)
    modelo = ModeloRoteiro(roteiro=[("ModuleAnalysis", RUIM)])
    m = {}
    obj, erros = executar_agente("requisitos", _agente(modelo), "x", valida_escopo, m, 2,
                                 modulo="Users", leitura=_ambiente(tmp_path).leitura())

    assert erros == ["Duplicate requirement IDs: ['FR01']."]
    assert len(modelo.recebidas) == 2 * 3  # 2 tentativas x orçamento de 3 chamadas
    # a 2ª tentativa começa do zero, com os erros da última submissão no prompt
    assert "Duplicate requirement IDs" in modelo.recebidas[3][-1].content
    r = m["requisitos"]
    assert r["tentativas"] == 2 and r["reprovadas"] == 6 and r["sem_saida"] == 0
    assert r["falhas_finais"] == 1
    # a última submissão reprovada volta para o chamador gravar o .invalido.json
    assert type(obj) is ModuleAnalysis and obj.scope_summary == "ruim"


def test_sem_validacao_aceita_qualquer_submissao(tmp_path, monkeypatch):
    monkeypatch.setattr("execucao.VALIDAR", False)
    modelo = ModeloRoteiro(roteiro=[("ModuleAnalysis", RUIM)])
    obj, erros = executar_agente("t", _agente(modelo), "x", valida_escopo, {}, 1)
    assert erros == [] and obj.scope_summary == "ruim"


def test_normalizacao_antes_da_validacao_no_loop(tmp_path):
    def normalizar(o):
        o.scope_summary = "ok"  # corrige em código, sem gastar nova chamada

    modelo = ModeloRoteiro(roteiro=[("ModuleAnalysis", RUIM)])
    m = {}
    obj, erros = executar_agente("t", _agente(modelo), "x", valida_escopo, m, 1,
                                 normalizar=normalizar)
    assert erros == [] and m["t"]["reprovadas"] == 0 and len(modelo.recebidas) == 1


def test_sem_autonomia_nao_ha_tools():
    modelo = ModeloRoteiro(roteiro=[("ModuleAnalysis", VALIDA)])
    agente = base.criar_agente_estruturado("Role: test.", ModuleAnalysis,
                                           ferramentas.ESPECIFICACAO, modelo=modelo,
                                           autonomia=False)
    obj, _ = executar_agente("t", agente, "x", lambda o: [], {}, 1)
    assert obj.scope_summary == "ok"
    assert "get_artifact" not in modelo.ferramentas_vistas


def test_instrucoes_de_autonomia_no_prompt():
    modelo = ModeloRoteiro(roteiro=[("ModuleAnalysis", VALIDA)])
    executar_agente("t", _agente(modelo), "x", lambda o: [], {}, 1)
    sistema = modelo.recebidas[0][0].content
    assert sistema.startswith("Role: test.")
    assert "AUTONOMY" in sistema and "get_artifact, get_contracts" in sistema
    assert "output tool ModuleAnalysis" in sistema


# --------------------------------------------------------- tools
def test_tools_usam_a_sessao(tmp_path):
    amb = _ambiente(tmp_path)
    amb.publicar("Users", "arquiteto_software", arquitetura())
    arquivo = amb.raiz_codigo("frontend") / "src" / "api" / "http.ts"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_text("export function http(url: string): string { return url; }\n")

    assert ferramentas.get_contracts.invoke({}) == ferramentas._SEM_AMBIENTE
    s = ferramentas.Sessao(leitura=amb.leitura(), modulo="Users", lado="frontend")
    with ferramentas.sessao(s):
        contratos = ferramentas.get_contracts.invoke({})
        assert contratos.startswith("Catalog") and "\nUsers" not in contratos  # sem o próprio
        assert "endpoints: GET /api/v1/products" in contratos
        assert ferramentas.list_files.invoke({}) == "src/api/http.ts"  # lado da sessão
        assert "No backend files" in ferramentas.list_files.invoke({"side": "backend"})
        assert "export function http" in ferramentas.read_files.invoke(
            {"paths": ["src/api/http.ts"]})
        assert "http" in ferramentas.get_signatures.invoke({"paths": ["src/api/http.ts"]})
        assert "Unknown artifact" in ferramentas.get_artifact.invoke(
            {"module": "Users", "artifact": "code"})
    assert ferramentas.sessao_atual() is None


@pytest.mark.parametrize("papel, esperado", [
    ("requisitos", {"get_artifact", "get_contracts"}),
    ("planejador", {"get_artifact", "get_contracts", "list_files", "get_signatures",
                    "ask_agent", "report_blocker"}),
    ("implementador_backend",
     {"get_artifact", "get_contracts", "list_files", "read_files", "get_signatures",
      "ask_agent"}),
])
def test_tools_por_papel(papel, esperado, monkeypatch):
    import importlib
    capturado = {}
    monkeypatch.setattr(base, "_agente_cache",
                        lambda regras, schema, ctx, tools: capturado.setdefault("t", tools))
    importlib.import_module(f"agents.{papel}").get_agente(contexto())
    assert set(capturado["t"]) == esperado


def test_cria_os_agentes_reais_com_tools(monkeypatch):
    """Monta os agentes de verdade (só o modelo é falso): pega erros de montagem,
    como tools não hashable na chave do cache."""
    from pipeline import criar_agentes
    monkeypatch.setattr(base, "_modelo", lambda: ModeloRoteiro(roteiro=[("x", {})]))
    base._agente_cache.cache_clear()
    try:
        ags = criar_agentes(contexto())
    finally:
        base._agente_cache.cache_clear()
    assert len(ags) == 7
    assert all(a.limite_passos == 4 * base.LIMITE_CHAMADAS_AGENTE + 10 for a in ags.values())


def test_acessos_dos_agentes_ficam_registrados(tmp_path, capsys):
    amb = _ambiente(tmp_path)
    modelo = ModeloRoteiro(roteiro=[
        ("get_artifact", {"module": "Catalog", "artifact": "architecture"}),
        ("ModuleAnalysis", RUIM),
        ("ModuleAnalysis", VALIDA),
    ])
    executar_agente("requisitos", _agente(modelo), "x", valida_escopo, {}, 1,
                    modulo="Users", leitura=amb.leitura())
    acessos = amb.acessos()
    assert [(a["etapa"], a["modulo"], a["acao"]) for a in acessos] == [
        ("requisitos", "Users", "get_artifact"),
        ("requisitos", "Users", "submit:ModuleAnalysis"),
        ("requisitos", "Users", "submit:ModuleAnalysis"),
    ]
    assert acessos[0]["args"] == {"module": "Catalog", "artifact": "architecture"}
    assert acessos[0]["resultado_chars"] > 100
    assert acessos[1]["resultado"].startswith("REJECTED: Duplicate requirement IDs")
    assert acessos[2]["resultado"] == "approved"
    saida = capsys.readouterr().out
    assert "· requisitos → get_artifact(module='Catalog', artifact='architecture')" in saida


class ModeloLeitor(BaseChatModel):
    """Quer ler o ambiente para sempre: chama get_contracts enquanto a tool existir;
    quando só a de saída estiver disponível, submete. Guarda as tools de cada chamada."""
    tools_por_chamada: list = []
    sistemas: list = []

    @property
    def _llm_type(self):
        return "leitor"

    def bind_tools(self, tools, **kw):
        self.tools_por_chamada.append([getattr(t, "name", None) or t["function"]["name"]
                                       for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.sistemas.append(messages[0].content)
        n = len(self.sistemas)
        if "get_contracts" in self.tools_por_chamada[-1]:
            chamada = {"name": "get_contracts", "args": {}, "id": f"c{n}"}
        else:
            chamada = {"name": "ModuleAnalysis", "args": VALIDA, "id": f"c{n}"}
        return ChatResult(generations=[ChatGeneration(
            message=AIMessage(content="", tool_calls=[chamada]))])


def test_fim_do_orcamento_obriga_a_submeter(tmp_path, monkeypatch):
    """O agente que só lê é obrigado a entregar nas 2 últimas chamadas."""
    monkeypatch.setattr(base, "LIMITE_CHAMADAS_AGENTE", 5)
    modelo = ModeloLeitor()
    m = {}
    obj, erros = executar_agente("t", _agente(modelo), "x", lambda o: [], m, 1,
                                 modulo="Users", leitura=_ambiente(tmp_path).leitura())
    assert erros == [] and obj.scope_summary == "ok"
    assert len(modelo.sistemas) == 4  # 3 leituras + a submissão obrigatória
    assert "get_contracts" in modelo.tools_por_chamada[2]
    assert modelo.tools_por_chamada[3] == ["ModuleAnalysis"]  # só a tool de saída
    assert "BUDGET EXHAUSTED" in modelo.sistemas[3]
    assert "BUDGET EXHAUSTED" not in modelo.sistemas[2]
    assert m["t"]["chamadas_tool"] == 3 and m["t"]["sem_saida"] == 0


def test_submissao_reprovada_no_fim_do_orcamento_ainda_pode_ser_corrigida(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "LIMITE_CHAMADAS_AGENTE", 4)
    modelo = ModeloRoteiro(roteiro=[
        ("get_contracts", {}),
        ("get_contracts", {}),
        ("ModuleAnalysis", RUIM),    # 1ª chamada da reserva: reprovada
        ("ModuleAnalysis", VALIDA),  # 2ª chamada da reserva: corrigida
    ])
    m = {}
    obj, erros = executar_agente("t", _agente(modelo), "x", valida_escopo, m, 1,
                                 modulo="Users", leitura=_ambiente(tmp_path).leitura())
    assert erros == [] and obj.scope_summary == "ok"
    assert len(modelo.recebidas) == 4 and m["t"]["reprovadas"] == 1
    assert modelo.ferramentas_vistas == ["ModuleAnalysis"]  # na reserva, só a de saída
    assert "REJECTED" in _tool_msgs(modelo.recebidas[3])[-1].content
