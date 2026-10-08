import json

import pytest

from pipeline import PipelineError, processar_modulo
from schemas import (ArchitecturalDecision, Column, Endpoint, Entity,
                     ExternalReference, FinalSpecification, Index,
                     ModuleAnalysis, ModuleArchitecture, ModuleDataModel,
                     Problem, Relationship, Requirement, ResolvedProblem)
import validation


class AgenteFalso:
    """Devolve respostas em sequência e guarda as mensagens recebidas."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.configs = []

    def invoke(self, entrada, config=None):
        self.chamadas.append(entrada["messages"][0]["content"])
        self.configs.append(config)
        r = self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]
        return {"structured_response": r, "messages": []}


# --------------------------------------------------------- fábricas
def analise(n_problemas=2):
    return ModuleAnalysis(
        module_name="Users", scope_summary="x",
        problems=[Problem(id=f"PROB0{i}", title="t", type="gap",
                          severity="high", description="d", correction_suggestion="s")
                  for i in range(1, n_problemas + 1)],
        functional_requirements=[Requirement(id="FR01", description="Register a user")],
        non_functional_requirements=[Requirement(id="NFR01", description="Block in 5 fails")],
        dependencies=[], observations=[])


def resolvido(id_, conf="high"):
    return ResolvedProblem(id=id_, title="t", type="gap", severity="high",
                           problem="p", adopted_decision="d", justification="j",
                           confidence=conf, requirements_impact="i")


def spec(ids=("PROB01", "PROB02"), conf="high", alertas=()):
    return FinalSpecification(
        module_name="Users", final_scope="e",
        resolved_problems=[resolvido(i, conf) for i in ids],
        functional_requirements=[Requirement(id="FR01", description="a")],
        non_functional_requirements=[Requirement(id="NFR01", description="b")],
        interdependencies=[], review_alerts=list(alertas),
        consolidated_specification="summary")


def col(name, sql_type="UUID", pk=False, ref=None, required=True):
    return Column(name=name, sql_type=sql_type, required=required,
                  primary_key=pk, reference=ref, constraints=[])


def base_cols(pk_tipo="UUID"):
    return [col("id", pk_tipo, pk=True), col("tenant_id"),
            col("created_at", "TIMESTAMP WITH TIME ZONE"),
            col("updated_at", "TIMESTAMP WITH TIME ZONE")]


def modelo(pk_tipo="UUID", ref=None, ix_req="FR01", ix_col="email",
           sem_base=False, rel_alvo="roles"):
    users = base_cols(pk_tipo) + [col("email", "VARCHAR(254)")]
    if sem_base:
        users = [c for c in users if c.name != "updated_at"]
    if ref:
        users.append(col("x_id", ref=ref, required=False))
    roles = base_cols() + [col("name", "VARCHAR(60)")]
    user_roles = base_cols() + [col("user_id", ref="users.id"),
                                col("role_id", ref="roles.id")]
    return ModuleDataModel(
        module_name="Users",
        entities=[
            Entity(table="users", purpose="u", columns=users, constraints=[],
                   indexes=[Index(name="ux_users_tenant_email",
                                  columns=["tenant_id", ix_col], unique=True,
                                  where=None, requirement=ix_req)]),
            Entity(table="roles", purpose="r", columns=roles, indexes=[], constraints=[]),
            Entity(table="user_roles", purpose="ur", columns=user_roles, indexes=[],
                   constraints=["UNIQUE (tenant_id, user_id, role_id)"]),
        ],
        relationships=[Relationship(source="users", target=rel_alvo, cardinality="N:N",
                                    via="user_roles", description="papéis")],
        external_references=[],
        integrity_rules=[], assumptions=[], data_model_summary="m")


def arquitetura(caminho="/api/v1/users", req="FR01", tabelas=("users",),
                pacote="com.distributor.erp.users", dec_req="NFR01", roles=("ADMIN",)):
    return ModuleArchitecture(
        module_name="Users", java_package=pacote, package_structure=["api/"],
        endpoints=[Endpoint(method="GET", path=caminho, purpose="f",
                            requires_authentication=True, roles=list(roles),
                            request_summary="-", response_summary="lista",
                            http_status_codes=[200], tables_touched=list(tabelas),
                            addressed_requirements=[req])],
        public_services=[], domain_events=[], module_dependencies=[],
        architectural_decisions=[ArchitecturalDecision(
            topic="transactions", decision="d", reason="m", requirement=dec_req)],
        architecture_summary="r")


# --------------------------------------------------------- validações: especificação
def test_spec_ok():
    assert validation.validar_especificacao(spec(), analise()) == []


def test_spec_problema_faltando():
    erros = validation.validar_especificacao(spec(ids=("PROB01",)), analise())
    assert any("PROB02" in e for e in erros)


def test_spec_baixa_sem_alerta():
    erros = validation.validar_especificacao(spec(conf="low"), analise())
    assert any("review_alerts" in e for e in erros)


# --------------------------------------------------------- validações: modelo de dados
def test_modelo_ok():
    assert validation.validar_modelo_dados(modelo(), spec()) == []


def test_modelo_pk_deve_ser_uuid():
    erros = validation.validar_modelo_dados(modelo(pk_tipo="BIGINT"), spec())
    assert any("id UUID" in e for e in erros)


def test_modelo_fk_externa_proibida():
    erros = validation.validar_modelo_dados(modelo(ref="customers.id"), spec())
    assert any("não pertence a este módulo" in e for e in erros)


def test_modelo_fk_coluna_inexistente():
    erros = validation.validar_modelo_dados(modelo(ref="roles.codigo"), spec())
    assert any("coluna inexistente" in e for e in erros)


def test_modelo_reference_string_null_e_ignorada():
    assert validation.validar_modelo_dados(modelo(ref="null"), spec()) == []


def test_modelo_colunas_base():
    erros = validation.validar_modelo_dados(modelo(sem_base=True), spec())
    assert any("updated_at" in e for e in erros)


def test_modelo_indice_requisito_e_coluna():
    erros = validation.validar_modelo_dados(modelo(ix_req="FR99", ix_col="foo"), spec())
    assert any("FR99" in e for e in erros)
    assert any("foo" in e for e in erros)


def test_modelo_relacionamento_tabela_inexistente():
    erros = validation.validar_modelo_dados(modelo(rel_alvo="permissions"), spec())
    assert any("permissions" in e for e in erros)


def test_modelo_external_reference_coluna_inexistente():
    m = modelo()
    m.external_references.append(ExternalReference(
        table="users", column="customer_id", module="Customers", reason="r"))
    erros = validation.validar_modelo_dados(m, spec())
    assert any("users.customer_id" in e for e in erros)


# --------------------------------------------------------- validações: arquitetura
def test_arquitetura_ok():
    assert validation.validar_arquitetura(arquitetura(), spec(), modelo()) == []


def test_arquitetura_caminho_e_requisito():
    erros = validation.validar_arquitetura(
        arquitetura("/users", "FR99"), spec(), modelo())
    assert len(erros) == 2


def test_arquitetura_kebab_case():
    erros = validation.validar_arquitetura(
        arquitetura("/api/v1/user_roles/{userId}"), spec(), modelo())
    assert any("kebab-case" in e for e in erros)
    assert validation.validar_arquitetura(
        arquitetura("/api/v1/user-roles/{userId}"), spec(), modelo()) == []


def test_arquitetura_tabela_fora_do_modelo():
    erros = validation.validar_arquitetura(
        arquitetura(tabelas=("users", "sessions")), spec(), modelo())
    assert any("sessions" in e for e in erros)


def test_arquitetura_pacote_decisao_e_roles():
    erros = validation.validar_arquitetura(
        arquitetura(pacote="com.empresa.users", dec_req="NFR42", roles=()),
        spec(), modelo())
    assert any("java_package" in e for e in erros)
    assert any("NFR42" in e for e in erros)
    assert any("roles" in e for e in erros)


# --------------------------------------------------------- pipeline
def agentes(esc=None, mod=None, arq=None, plan=None, back=None, front=None):
    from tests.fakes import implementador_falso, planejador_falso
    return {"requisitos": AgenteFalso(analise()),
            "esclarecedor": esc or AgenteFalso(spec()),
            "modelador_dados": mod or AgenteFalso(modelo()),
            "arquiteto_software": arq or AgenteFalso(arquitetura()),
            "planejador": plan or planejador_falso(),
            "implementador_backend": back or implementador_falso(),
            "implementador_frontend": front or implementador_falso()}


def test_pipeline_completo(tmp_path):
    ags = agentes()
    res = processar_modulo("Usuários", "desc", agentes=ags, saida_dir=str(tmp_path))
    pasta = tmp_path / "usuarios"
    for f in ["1_analise.json", "2_especificacao.json", "3_modelo_dados.json",
              "4_arquitetura.json", "relatorio.md", "metricas.json"]:
        assert (pasta / f).exists(), f
    rel = (pasta / "relatorio.md").read_text()
    assert "Módulo: Usuários" in rel
    assert "Modelo de dados" in rel and "Arquitetura do módulo" in rel
    json.loads((pasta / "4_arquitetura.json").read_text())
    assert res["metricas"]["modelador_dados"]["tentativas"] == 1
    assert res["metricas"]["arquiteto_software"]["tentativas"] == 1
    # o arquiteto recebe o modelo de dados validado
    assert "VALIDATED DATA MODEL" in ags["arquiteto_software"].chamadas[0]
    assert "user_roles" in ags["arquiteto_software"].chamadas[0]


def test_retry_corrige(tmp_path):
    esc = AgenteFalso(spec(ids=("PROB01",)), spec())  # 1ª falha, 2ª ok
    res = processar_modulo("Usuários", "d", agentes=agentes(esc=esc), saida_dir=str(tmp_path))
    assert res["metricas"]["esclarecedor"]["tentativas"] == 2
    assert "PROB02" in esc.chamadas[1]  # feedback de erro foi enviado


def test_retry_modelador(tmp_path):
    mod = AgenteFalso(modelo(pk_tipo="BIGINT"), modelo())
    res = processar_modulo("Usuários", "d", agentes=agentes(mod=mod), saida_dir=str(tmp_path))
    assert res["metricas"]["modelador_dados"]["tentativas"] == 2
    assert "id UUID" in mod.chamadas[1]


def test_falha_definitiva(tmp_path):
    esc = AgenteFalso(spec(ids=("PROB01",)))  # sempre inválida
    with pytest.raises(PipelineError):
        processar_modulo("Usuários", "d", agentes=agentes(esc=esc),
                         saida_dir=str(tmp_path), max_tentativas=2)
    assert (tmp_path / "usuarios" / "esclarecedor.invalido.json").exists()


def test_falha_definitiva_arquiteto(tmp_path):
    arq = AgenteFalso(arquitetura(tabelas=("inventadas",)))
    with pytest.raises(PipelineError):
        processar_modulo("Usuários", "d", agentes=agentes(arq=arq),
                         saida_dir=str(tmp_path), max_tentativas=2)
    pasta = tmp_path / "usuarios"
    assert (pasta / "arquiteto_software.invalido.json").exists()
    assert (pasta / "3_modelo_dados.json").exists()  # etapa anterior preservada


def test_ate_esclarecedor(tmp_path):
    res = processar_modulo("Usuários", "d", ate="esclarecedor",
                           agentes=agentes(), saida_dir=str(tmp_path))
    assert "modelo_dados" not in res and "arquitetura" not in res


def test_ate_modelador(tmp_path):
    res = processar_modulo("Usuários", "d", ate="modelador_dados",
                           agentes=agentes(), saida_dir=str(tmp_path))
    assert "modelo_dados" in res and "arquitetura" not in res
