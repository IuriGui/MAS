"""Comunicação entre agentes (F3): consultas, bloqueios, revisões e pendências.

Os agentes falsos chamam as tools reais (ask_agent, report_blocker) dentro da
sessão que o orquestrador abre, como um agente de verdade faria.
"""
import json

import pytest

import pipeline
from agents import ferramentas
from ambiente import Ambiente
from pipeline import processar_modulo
from schemas import ConsultationAnswer
from tests.fakes import planejador_falso
from tests.test_pipeline import AgenteFalso, agentes, arquitetura, modelo, spec


class AgenteRoteiro:
    """Em cada chamada, executa as ações (tools reais) e devolve a resposta.
    Depois do roteiro, repete o último passo."""

    def __init__(self, *passos):
        self.passos = list(passos)
        self.chamadas = []
        self.retornos = []

    def invoke(self, entrada, config=None):
        self.chamadas.append(entrada["messages"][0]["content"])
        acoes, resposta = self.passos.pop(0) if len(self.passos) > 1 else self.passos[0]
        for tool, args in acoes:
            self.retornos.append(getattr(ferramentas, tool).invoke(args))
        return {"structured_response": resposta, "messages": []}


BLOQUEIO_MODELO = ("report_blocker", {"recipient": "data_modeler",
                                      "problem": "Table sessions is missing."})


def _rodar(tmp_path, ate="arquiteto_software", **kw):
    max_revisoes = kw.pop("max_revisoes", 2)
    return processar_modulo("Usuários", "d", agentes=agentes(**kw), ate=ate,
                            saida_dir=str(tmp_path), max_revisoes=max_revisoes)


def _mensagens(tmp_path):
    return Ambiente(tmp_path, registro=tmp_path / "usuarios" / "ambiente.jsonl").mensagens()


# --------------------------------------------------------- bloqueio e revisão
def test_bloqueio_reabre_a_etapa_e_roda_de_novo_quem_bloqueou(tmp_path):
    mod = AgenteFalso(modelo())
    arq = AgenteRoteiro(([BLOQUEIO_MODELO], arquitetura()), ([], arquitetura()))
    res = _rodar(tmp_path, mod=mod, arq=arq)

    assert len(mod.chamadas) == 2 and len(arq.chamadas) == 2
    revisao = mod.chamadas[1]
    assert "REVISION REQUEST from the software_architect" in revisao
    assert "Table sessions is missing." in revisao
    assert "YOUR PREVIOUS OUTPUT (JSON)" in revisao
    assert "Blocker sent to the data_modeler" in arq.retornos[0]

    msgs = _mensagens(tmp_path)
    assert [(m.sender, m.recipient, m.kind) for m in msgs] == [
        ("arquiteto_software", "modelador_dados", "blocker"),
        ("modelador_dados", "arquiteto_software", "resolution"),
    ]
    assert res["pendencias"] == []
    assert res["metricas"]["modelador_dados_revisao"]["tentativas"] == 1
    reg = Ambiente(tmp_path, registro=tmp_path / "usuarios" / "ambiente.jsonl").registro()
    assert [r["autor"] for r in reg] == [
        "requisitos", "esclarecedor", "modelador_dados", "arquiteto_software",
        "modelador_dados_revisao", "arquiteto_software"]


def test_limite_de_revisoes_vira_pendencia(tmp_path):
    mod = AgenteFalso(modelo())
    arq = AgenteRoteiro(([BLOQUEIO_MODELO], arquitetura()))
    res = _rodar(tmp_path, mod=mod, arq=arq, max_revisoes=0)

    assert len(mod.chamadas) == 1 and len(arq.chamadas) == 1
    assert [p["motivo"] for p in res["pendencias"]] == ["limite de revisões do módulo"]
    assert res["pendencias"][0]["problemas"] == ["Table sessions is missing."]
    assert [m.kind for m in _mensagens(tmp_path)] == ["blocker", "unresolved"]
    assert (tmp_path / "usuarios" / "4_arquitetura.json").exists()  # o módulo seguiu


def test_revisao_reprovada_mantem_o_artefato_e_vira_pendencia(tmp_path):
    ruim = modelo().model_copy(update={"entities": []})
    mod = AgenteFalso(modelo(), ruim)
    arq = AgenteRoteiro(([BLOQUEIO_MODELO], arquitetura()))
    res = processar_modulo("Usuários", "d", agentes=agentes(mod=mod, arq=arq),
                           ate="arquiteto_software", saida_dir=str(tmp_path),
                           max_tentativas=1)

    assert [p["motivo"] for p in res["pendencias"]] == ["a revisão não passou na validação"]
    amb = Ambiente(tmp_path)
    assert amb.artefato("Usuários", "modelo_dados") == modelo()  # o anterior vale
    assert (tmp_path / "usuarios" / "modelador_dados_revisao.invalido.json").exists()
    assert len(arq.chamadas) == 1  # sem revisão, quem bloqueou não roda de novo


def test_bloqueio_em_cadeia(tmp_path):
    """Arquiteto bloqueia o Modelador; ao revisar, o Modelador bloqueia o Esclarecedor."""
    esc = AgenteFalso(spec())
    mod = AgenteRoteiro(
        ([], modelo()),
        ([("report_blocker", {"recipient": "clarifier",
                              "problem": "FR01 does not say if users are deleted."})],
         modelo()),
        ([], modelo()))
    arq = AgenteRoteiro(([BLOQUEIO_MODELO], arquitetura()), ([], arquitetura()))
    res = _rodar(tmp_path, esc=esc, mod=mod, arq=arq)

    assert len(esc.chamadas) == 2   # normal + revisão
    assert len(mod.chamadas) == 3   # normal + revisão + de novo com a spec revisada
    assert len(arq.chamadas) == 2   # normal + de novo com o modelo revisado
    assert "REVISION REQUEST from the data_modeler" in esc.chamadas[1]
    kinds = [(m.sender, m.kind) for m in _mensagens(tmp_path)]
    # o Modelador revisou (e avisou o Arquiteto) antes de o bloqueio dele ser atendido
    assert kinds == [("arquiteto_software", "blocker"), ("modelador_dados", "blocker"),
                     ("modelador_dados", "resolution"), ("esclarecedor", "resolution")]
    assert res["pendencias"] == []


def test_planejador_bloqueia_o_arquiteto(tmp_path):
    plan_base = planejador_falso()

    class Planejador:
        def __init__(self):
            self.modulo = 0
            self.chamadas = []

        def invoke(self, entrada, config=None):
            msg = entrada["messages"][0]["content"]
            self.chamadas.append(msg)
            if "MODE: MODULE PLAN" in msg:
                self.modulo += 1
                if self.modulo == 1:
                    ferramentas.report_blocker.invoke(
                        {"recipient": "software_architect",
                         "problem": "No endpoint to deactivate a user (FR01)."})
            return plan_base.invoke(entrada, config)

    plan = Planejador()
    arq = AgenteFalso(arquitetura())
    res = _rodar(tmp_path, ate="planejador", plan=plan, arq=arq)
    assert plan.modulo == 2 and len(arq.chamadas) == 2
    assert "REVISION REQUEST from the planner" in arq.chamadas[1]
    assert (tmp_path / "usuarios" / "5_plano.json").exists()
    assert res["pendencias"] == []


# --------------------------------------------------------- consulta
def test_consulta_ao_dono_do_artefato(tmp_path):
    consultor = AgenteFalso(ConsultationAnswer(
        answer="password_hash is nullable for SSO users (FR03).",
        defect_in_my_artifact=False))
    arq = AgenteRoteiro(([("ask_agent", {"recipient": "data_modeler",
                                         "question": "Why is password_hash nullable?"})],
                         arquitetura()))
    ags = agentes(arq=arq)
    ags["consulta:modelador_dados"] = consultor
    res = processar_modulo("Usuários", "d", agentes=ags, ate="arquiteto_software",
                           saida_dir=str(tmp_path))

    assert arq.retornos == ["password_hash is nullable for SSO users (FR03)."]
    pergunta = consultor.chamadas[0]
    assert "QUESTION FROM THE SOFTWARE_ARCHITECT" in pergunta
    assert "Why is password_hash nullable?" in pergunta
    assert '"entities"' in pergunta  # o artefato do Modelador vai junto
    msgs = _mensagens(tmp_path)
    assert [(m.sender, m.recipient, m.kind) for m in msgs] == [
        ("arquiteto_software", "modelador_dados", "question"),
        ("modelador_dados", "arquiteto_software", "answer")]
    assert msgs[1].in_reply_to == msgs[0].id
    assert res["metricas"]["modelador_dados_consulta"]["tentativas"] == 1
    assert len(arq.chamadas) == 1  # consulta não reabre nada


def test_resposta_que_admite_defeito_sugere_bloqueio(tmp_path):
    consultor = AgenteFalso(ConsultationAnswer(
        answer="It should be there.", defect_in_my_artifact=True,
        defect_description="Table sessions is missing."))
    arq = AgenteRoteiro(([("ask_agent", {"recipient": "data_modeler", "question": "?"})],
                         arquitetura()))
    ags = agentes(arq=arq)
    ags["consulta:modelador_dados"] = consultor
    processar_modulo("Usuários", "d", agentes=ags, ate="arquiteto_software",
                     saida_dir=str(tmp_path))
    assert "acknowledges a defect" in arq.retornos[0]
    assert "call report_blocker" in arq.retornos[0]


def test_destinatario_nao_permitido_e_orcamento_de_perguntas(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_CONSULTAS_AGENTE", 1)
    consultor = AgenteFalso(ConsultationAnswer(answer="ok", defect_in_my_artifact=False))
    perguntas = [("ask_agent", {"recipient": "planner", "question": "?"}),
                 ("report_blocker", {"recipient": "requirements_analyst", "problem": "x"}),
                 ("ask_agent", {"recipient": "clarifier", "question": "1"}),
                 ("ask_agent", {"recipient": "clarifier", "question": "2"})]
    arq = AgenteRoteiro((perguntas, arquitetura()))
    ags = agentes(arq=arq)
    ags["consulta:esclarecedor"] = consultor
    processar_modulo("Usuários", "d", agentes=ags, ate="arquiteto_software",
                     saida_dir=str(tmp_path))
    assert "cannot address 'planner'" in arq.retornos[0]
    assert "cannot address 'requirements_analyst'" in arq.retornos[1]
    assert arq.retornos[2] == "ok"
    assert "budget exhausted" in arq.retornos[3]
    assert len(consultor.chamadas) == 1


def test_sem_comunicacao_nao_ha_canal(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "COMUNICACAO", False)
    mod = AgenteFalso(modelo())
    arq = AgenteRoteiro(([BLOQUEIO_MODELO], arquitetura()))
    res = _rodar(tmp_path, mod=mod, arq=arq)
    assert arq.retornos == [ferramentas._SEM_CANAL]
    assert len(mod.chamadas) == 1 and res["pendencias"] == []
    assert _mensagens(tmp_path) == []


# --------------------------------------------------------- tools e grafo
@pytest.mark.parametrize("etapa, comunicacao, esperado", [
    ("requisitos", True, set()),
    ("modelador_dados", True, {"ask_agent", "report_blocker"}),
    ("implementador_frontend", True, {"ask_agent"}),
    ("arquiteto_software", False, set()),
])
def test_tools_de_comunicacao_por_papel(etapa, comunicacao, esperado):
    nomes = {t.name for t in ferramentas.para(etapa, comunicacao)}
    assert nomes & {"ask_agent", "report_blocker"} == esperado


def test_instrucoes_mencionam_a_comunicacao():
    texto = ferramentas.instrucoes(ferramentas.para("arquiteto_software", True),
                                   "ModuleArchitecture", 12)
    assert "ask_agent" in texto and "report_blocker" in texto
    assert "report_blocker" not in ferramentas.instrucoes(
        ferramentas.para("implementador_backend", True), "GeneratedFiles", 12)


def test_diagrama_do_grafo():
    mermaid = pipeline.diagrama_mermaid()
    for no in pipeline.NOS + ("revisao",):
        assert no in mermaid


def test_resumo_conta_mensagens(tmp_path):
    import metricas
    mod = AgenteFalso(modelo())
    arq = AgenteRoteiro(([BLOQUEIO_MODELO], arquitetura()), ([], arquitetura()))
    _rodar(tmp_path / "execucao_01", mod=mod, arq=arq)
    (tmp_path / "execucao_01" / "execucao.json").write_text(json.dumps({"segundos": 1}))
    r = metricas.agregar([metricas.carregar_execucao(tmp_path / "execucao_01")])
    assert r["mensagens_entre_agentes"] == 2
    assert r["mensagens_por_tipo"] == {"blocker": 1, "resolution": 1}
    assert "Mensagens entre agentes: 2 (blocker: 1, resolution: 1)" in metricas.markdown(r)
