"""Métricas da linha de base (F0): registro por tentativa e agregação."""
import json

import pytest
from langchain_core.messages import AIMessage

import metricas
from execucao import PipelineError, executar_agente
from projeto import processar_projeto
from tests.test_pipeline import AgenteFalso, agentes, analise, spec
from tests.test_projeto import SISTEMA, contexto


# --------------------------------------------------------- regra
@pytest.mark.parametrize("erro, esperado", [
    ("Table 'users' is missing required columns: ['created_at'].",
     "Table '_' is missing required columns: [_]."),
    ("Endpoint GET /api/v1/users cites nonexistent requirements: ['FR99'].",
     "Endpoint _ cites nonexistent requirements: [_]."),
    ("decisions has 7 ADRs; maximum is 5.", "decisions has N ADRs; maximum is N."),
    ("Circular dependency between modules: Users -> Sales -> Users. Resolve it.",
     "Circular dependency between modules: _ Resolve it."),
    ("Relationship users 1:N roles cites the table 'roles', which is not in entities.",
     "Relationship _ cites the table '_', which is not in entities."),
])
def test_regra_tira_valores_concretos(erro, esperado):
    assert metricas.regra(erro) == esperado


def test_mesma_regra_para_valores_diferentes():
    a = metricas.regra("'a/X.java' cites tables outside the data model: ['t1'].")
    b = metricas.regra("'b/Y.java' cites tables outside the data model: ['t2', 't3'].")
    assert a == b


# --------------------------------------------------------- registro por tentativa
def test_registra_reprovacao_e_regra():
    respostas = iter([["Duplicate tables: ['users']."], []])
    m = {}
    executar_agente("t", AgenteFalso(spec()), "x", lambda o: next(respostas), m, 2)
    assert m["t"]["tentativas"] == 2
    assert m["t"]["reprovadas"] == 1 and m["t"]["sem_saida"] == 0
    assert m["t"]["falhas_finais"] == 0
    assert m["t"]["erros_por_regra"] == {"Duplicate tables: [_].": 1}


def test_registra_falha_final():
    m = {}
    obj, erros = executar_agente("t", AgenteFalso(spec()), "x", lambda o: ["ruim"], m, 3)
    assert erros and m["t"]["reprovadas"] == 3 and m["t"]["falhas_finais"] == 1
    assert m["t"]["erros_por_regra"] == {"ruim": 3}


def test_registra_sem_saida():
    m = {}
    with pytest.raises(PipelineError):
        executar_agente("t", AgenteFalso(None), "x", lambda o: [], m, 2)
    assert m["t"]["sem_saida"] == 2 and m["t"]["falhas_finais"] == 1


def test_conta_tool_calls_sem_a_saida_estruturada():
    class ComTools:
        def invoke(self, entrada, config=None):
            msg = AIMessage(content="", tool_calls=[
                {"name": "ler_arquivo", "args": {}, "id": "1"},
                {"name": "consultar_contrato", "args": {}, "id": "2"},
                {"name": "ModuleAnalysis", "args": {}, "id": "3"},  # saída estruturada
            ])
            return {"structured_response": analise(), "messages": [msg]}

    m = {}
    executar_agente("t", ComTools(), "x", lambda o: [], m, 1)
    assert m["t"]["chamadas_tool"] == 2


def test_metricas_antigas_ganham_campos_novos():
    m = {"t": {"tentativas": 4, "segundos": 1.0, "input_tokens": 0, "output_tokens": 0}}
    executar_agente("t", AgenteFalso(spec()), "x", lambda o: [], m, 1)
    assert m["t"]["tentativas"] == 5 and m["t"]["reprovadas"] == 0


# --------------------------------------------------------- agregação
def _rodar(pasta, falhar_users: bool):
    chamadas = {"n": 0}

    def fabrica(ctx):
        chamadas["n"] += 1
        if falhar_users and chamadas["n"] == 2:  # 1ª = base; 2ª = Users
            return agentes(esc=AgenteFalso(spec(ids=("PROB01",))))
        return agentes()

    pasta.mkdir(parents=True)
    processar_projeto("Loja", SISTEMA, contexto=contexto(), fabrica_agentes=fabrica,
                      saida_dir=str(pasta))
    (pasta / "execucao.json").write_text(json.dumps(
        {"commit": "abc123", "modelo": "falso", "segundos": 1.5,
         "config": {"MAX_TENTATIVAS": 2}, "erro": None}))


def test_agrega_execucoes(tmp_path):
    _rodar(tmp_path / "execucao_01", falhar_users=True)
    _rodar(tmp_path / "execucao_02", falhar_users=False)
    execs = [metricas.carregar_execucao(p) for p in sorted(tmp_path.iterdir())]

    assert set(execs[0]["unidades"]) == {"base", "Users", "Catalog", "Sales"}
    assert execs[0]["status"]["Users"] == {"status": "falhou",
                                           "etapa_falha": "esclarecedor"}

    r = metricas.agregar(execs)
    assert r["execucoes"] == 2 and r["modelos"] == ["falso"]
    assert r["por_modulo"]["Users"] == {"ok": 1, "execucoes": 2,
                                        "etapas_falha": {"esclarecedor": 1}}
    assert r["por_modulo"]["Sales"]["ok"] == 2
    assert [e["modulos_ok"] for e in r["por_execucao"]] == [2, 3]

    esc = r["por_etapa"]["esclarecedor"]
    assert esc["reprovadas"]["max"] == 2 and esc["reprovadas"]["min"] == 0
    assert esc["falhas_finais"]["media"] == 0.5
    assert esc["taxa_reprovacao"] > 0
    assert r["erros_por_regra"][0] == {
        "etapa": "esclarecedor",
        "regra": "Problems without resolution: [_]. Resolve ALL of them.",
        "ocorrencias": 2}

    md = metricas.markdown(r)
    assert "# Linha de base" in md
    assert "| Users | 1/2 | esclarecedor (1x) |" in md
    assert "Mensagens entre agentes: 0" in md
    assert r["tools_por_etapa"] == []  # agentes falsos não chamam tools


def test_ignora_execucao_incompleta(tmp_path):
    _rodar(tmp_path / "execucao_01", falhar_users=False)
    _rodar(tmp_path / "execucao_02", falhar_users=False)
    (tmp_path / "execucao_02" / "execucao.json").unlink()  # ainda rodando
    r = metricas.agregar([metricas.carregar_execucao(p) for p in sorted(tmp_path.iterdir())])
    assert r["execucoes"] == 1 and r["incompletas"] == ["execucao_02"]
    assert r["por_etapa"]["requisitos"]["tentativas"]["desvio"] == 0
    assert "ignoradas por estarem incompletas: execucao_02" in metricas.markdown(r)
