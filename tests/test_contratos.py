"""Contratos enxutos: get_contracts resumido, detalhe por módulo e tipos encurtados."""
import json

import pytest

from agents import ferramentas
from ambiente import Ambiente, encurtar_tipos
from schemas import DomainEvent, PublicService
from tests.test_pipeline import arquitetura
from tests.test_projeto import contexto

DTO = "com.distribuidora.erp.users.application.dto.UserSummaryDto"


# --------------------------------------------------------- encurtar tipos
@pytest.mark.parametrize("entrada, esperado", [
    # simples
    ("java.util.UUID", "UUID"),
    ("com.distribuidora.erp.audit.application.AuditService", "AuditService"),
    # genéricos: a estrutura do tipo não pode se perder
    (f"java.util.Optional<{DTO}>", "Optional<UserSummaryDto>"),
    (f"java.util.List<{DTO}>", "List<UserSummaryDto>"),
    ("java.util.Map<java.util.UUID, java.lang.Long>", "Map<UUID, Long>"),
    (f"java.util.Map<java.util.UUID, java.util.List<{DTO}>>",
     "Map<UUID, List<UserSummaryDto>>"),
    (f"org.springframework.data.domain.Page<{DTO}>", "Page<UserSummaryDto>"),
    # listas de parâmetros
    ("java.util.UUID tenantId, java.util.Set<java.util.UUID> userIds",
     "UUID tenantId, Set<UUID> userIds"),
    # vetores e curingas
    ("java.lang.String[]", "String[]"),
    (f"java.util.List<? extends {DTO}>", "List<? extends UserSummaryDto>"),
    # o que não é nome qualificado fica como está
    ("UUID", "UUID"),
    ("boolean", "boolean"),
    ("void", "void"),
    ("AuditEntryCommand command", "AuditEntryCommand command"),
    ("e.g. fail-closed", "e.g. fail-closed"),
    ("version 1.2", "version 1.2"),
])
def test_encurtar_tipos(entrada, esperado):
    assert encurtar_tipos(entrada) == esperado


def test_encurtar_e_idempotente():
    tipo = f"java.util.Map<java.util.UUID, java.util.List<{DTO}>>"
    assert encurtar_tipos(encurtar_tipos(tipo)) == encurtar_tipos(tipo)


# --------------------------------------------------------- get_contracts
def _arq_users():
    a = arquitetura()
    a.public_services = [PublicService(
        interface="com.distribuidora.erp.users.application.UserQueryService",
        method="findById", parameters="java.util.UUID tenantId, java.util.UUID userId",
        return_type=f"java.util.Optional<{DTO}>", purpose="Find a user by id.",
        consumer_module="Sales")]
    a.domain_events = [DomainEvent(
        name="UserCreatedEvent", payload='{"userId": "UUID"}',
        trigger="After a user is created.", consumers=["Audit"], uses_outbox=True)]
    return a


def _ambiente(tmp_path):
    amb = Ambiente(tmp_path, contexto())
    amb.publicar("Users", "arquiteto_software", _arq_users())
    amb.publicar("Catalog", "arquiteto_software", arquitetura(caminho="/api/v1/products"))
    return amb


def test_resumo_tem_uma_linha_por_servico_com_tipos_curtos(tmp_path):
    resumo = _ambiente(tmp_path).leitura().contratos(exceto="Sales")
    linhas = resumo.splitlines()
    assert linhas[:4] == [
        "Users",
        "  service UserQueryService.findById(UUID tenantId, UUID userId) "
        "-> Optional<UserSummaryDto>",
        "  events: UserCreatedEvent",
        "  endpoints: GET /api/v1/users",
    ]
    assert "Catalog" in linhas and "  endpoints: GET /api/v1/products" in linhas
    assert "get_contracts(module=...)" in resumo
    # o resumo não traz propósito, payload nem gatilho
    assert "Find a user by id" not in resumo and "After a user is created" not in resumo


def test_resumo_e_bem_menor_que_o_contrato_completo(tmp_path):
    amb = _ambiente(tmp_path)
    completo = json.dumps(amb.contratos(exceto="Sales"), indent=2)
    assert len(amb.leitura().contratos(exceto="Sales")) < len(completo) / 2


def test_detalhe_de_um_modulo_tem_nomes_completos(tmp_path):
    detalhe = json.loads(_ambiente(tmp_path).leitura().contratos(exceto="Sales",
                                                                 modulo="users"))
    assert detalhe["module"] == "Users"
    servico = detalhe["public_services"][0]
    assert servico["return_type"] == f"java.util.Optional<{DTO}>"
    assert servico["purpose"] == "Find a user by id."
    assert detalhe["domain_events"][0]["trigger"] == "After a user is created."


def test_detalhe_de_modulo_sem_contrato(tmp_path):
    ler = _ambiente(tmp_path).leitura()
    assert "has not published" in ler.contratos(modulo="Finance")
    assert "['Users', 'Catalog']" in ler.contratos(modulo="Finance")
    # o próprio módulo também não aparece no detalhe
    assert "has not published" in ler.contratos(exceto="Users", modulo="Users")


def test_sem_contratos_publicados(tmp_path):
    assert Ambiente(tmp_path, contexto()).leitura().contratos() == \
        "No other module has published contracts yet."


def test_tool_get_contracts_resumo_e_detalhe(tmp_path):
    amb = _ambiente(tmp_path)
    s = ferramentas.Sessao(etapa="arquiteto_software", leitura=amb.leitura(),
                           modulo="Sales")
    with ferramentas.sessao(s):
        resumo = ferramentas.get_contracts.invoke({})
        detalhe = ferramentas.get_contracts.invoke({"module": " Users "})
    assert "Optional<UserSummaryDto>" in resumo
    assert json.loads(detalhe)["public_services"][0]["method"] == "findById"
    assert [a["args"] for a in amb.acessos()] == [{"module": ""}, {"module": " Users "}]
    assert "module" in ferramentas.get_contracts.args
