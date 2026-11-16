"""Etapas 1–4 (especificação): validação de cada saída e o pipeline do módulo."""
import json

import pytest

from mas import validacao
from mas.pipeline.execucao import PipelineError
from mas.pipeline.modulo import processar_modulo
from mas.schemas import ExternalReference
from tests.fakes import AgenteFalso, agentes, analise, arquitetura, modelo, spec


def test_spec_ok():
    assert validacao.validar_especificacao(spec(), analise()) == []


def test_spec_problema_faltando():
    erros = validacao.validar_especificacao(spec(ids=("PROB01",)), analise())
    assert any("PROB02" in e for e in erros)


def test_spec_baixa_sem_alerta():
    erros = validacao.validar_especificacao(spec(conf="low"), analise())
    assert any("review_alerts" in e for e in erros)


# --------------------------------------------------------- validações: modelo de dados
def test_modelo_ok():
    assert validacao.validar_modelo_dados(modelo(), spec()) == []


def test_modelo_pk_deve_ser_uuid():
    erros = validacao.validar_modelo_dados(modelo(pk_tipo="BIGINT"), spec())
    assert any("id UUID" in e for e in erros)


def test_modelo_fk_externa_proibida():
    erros = validacao.validar_modelo_dados(modelo(ref="customers.id"), spec())
    assert any("não pertence a este módulo" in e for e in erros)


def test_modelo_fk_coluna_inexistente():
    erros = validacao.validar_modelo_dados(modelo(ref="roles.codigo"), spec())
    assert any("coluna inexistente" in e for e in erros)


def test_modelo_reference_string_null_e_ignorada():
    assert validacao.validar_modelo_dados(modelo(ref="null"), spec()) == []


def test_modelo_colunas_base():
    erros = validacao.validar_modelo_dados(modelo(sem_base=True), spec())
    assert any("updated_at" in e for e in erros)


def test_modelo_indice_requisito_e_coluna():
    erros = validacao.validar_modelo_dados(modelo(ix_req="FR99", ix_col="foo"), spec())
    assert any("FR99" in e for e in erros)
    assert any("foo" in e for e in erros)


def test_modelo_relacionamento_tabela_inexistente():
    erros = validacao.validar_modelo_dados(modelo(rel_alvo="permissions"), spec())
    assert any("permissions" in e for e in erros)


def test_modelo_external_reference_coluna_inexistente():
    m = modelo()
    m.external_references.append(ExternalReference(
        table="users", column="customer_id", module="Customers", reason="r"))
    erros = validacao.validar_modelo_dados(m, spec())
    assert any("users.customer_id" in e for e in erros)


# --------------------------------------------------------- validações: arquitetura
def test_arquitetura_ok():
    assert validacao.validar_arquitetura(arquitetura(), spec(), modelo()) == []


def test_arquitetura_caminho_e_requisito():
    erros = validacao.validar_arquitetura(
        arquitetura("/users", "FR99"), spec(), modelo())
    assert len(erros) == 2


def test_arquitetura_kebab_case():
    erros = validacao.validar_arquitetura(
        arquitetura("/api/v1/user_roles/{userId}"), spec(), modelo())
    assert any("kebab-case" in e for e in erros)
    assert validacao.validar_arquitetura(
        arquitetura("/api/v1/user-roles/{userId}"), spec(), modelo()) == []


def test_arquitetura_tabela_fora_do_modelo():
    erros = validacao.validar_arquitetura(
        arquitetura(tabelas=("users", "sessions")), spec(), modelo())
    assert any("sessions" in e for e in erros)


def test_arquitetura_pacote_decisao_e_roles():
    erros = validacao.validar_arquitetura(
        arquitetura(pacote="com.empresa.users", dec_req="NFR42", roles=()),
        spec(), modelo())
    assert any("java_package" in e for e in erros)
    assert any("NFR42" in e for e in erros)
    assert any("roles" in e for e in erros)


# --------------------------------------------------------- pipeline
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
