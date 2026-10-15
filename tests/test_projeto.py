import json

import pytest

import validation
from pipeline import PipelineError, carregar_contexto
from projeto import gerar_contexto, ler_modulos, processar_projeto
from system_prompts import templates
from tests.test_pipeline import (AgenteFalso, agentes, arquitetura, modelo,
                                 spec)

SISTEMA = """\
SYSTEM DESCRIPTION
Um ERP pequeno.

MODULES
1. Users: user accounts and roles.
2. Catalog: products,
   categories and prices.
3. Sales: sales orders.

CONSTRAINTS
- Preferred stack: Spring Boot.
"""


def contexto(**mudancas):
    """Contexto do exemplo reduzido a Users -> Catalog -> Sales."""
    c = carregar_contexto().model_copy(deep=True)
    mapa = {m.name: m for m in c.module_map}
    c.module_map = [mapa["Users"], mapa["Catalog"], mapa["Sales"]]
    mapa["Sales"].depends_on = ["Users", "Catalog"]
    c.build_order = ["Users", "Catalog", "Sales"]
    for k, v in mudancas.items():
        setattr(c, k, v)
    return c


DECLARADOS = ["Users", "Catalog", "Sales"]


# --------------------------------------------------------- leitura da descrição
def test_ler_modulos():
    mods = ler_modulos(SISTEMA)
    assert [n for n, _ in mods] == DECLARADOS
    assert mods[1][1] == "products, categories and prices."


# --------------------------------------------------------- validação do contexto
def test_contexto_exemplo_valido():
    c = carregar_contexto()
    assert validation.validar_contexto(c, [m.name for m in c.module_map]) == []


def test_contexto_ok():
    assert validation.validar_contexto(contexto(), DECLARADOS) == []


def test_contexto_modulo_faltando():
    erros = validation.validar_contexto(contexto(), DECLARADOS + ["Finance"])
    assert any("Finance" in e for e in erros)


def test_contexto_ciclo():
    c = contexto()
    c.module_map[0].depends_on = ["Sales"]  # Users -> Sales -> Users
    erros = validation.validar_contexto(c, DECLARADOS)
    assert any("Circular dependency" in e for e in erros)


def test_contexto_build_order_fora_de_ordem():
    erros = validation.validar_contexto(
        contexto(build_order=["Sales", "Users", "Catalog"]), DECLARADOS)
    assert any("before its dependency" in e for e in erros)


def test_contexto_build_order_incompleto():
    erros = validation.validar_contexto(contexto(build_order=["Users", "Catalog"]), DECLARADOS)
    assert any("does not include" in e for e in erros)


def test_contexto_dependencia_inexistente():
    c = contexto()
    c.module_map[2].depends_on = ["Users", "Billing"]
    assert any("Billing" in e for e in validation.validar_contexto(c, DECLARADOS))


# --------------------------------------------------------- convenções do contexto
def test_convencoes_do_exemplo_iguais_ao_padrao():
    assert validation.convencoes(carregar_contexto()) == validation.PADRAO


def test_convencoes_sem_multitenancy():
    c = contexto()
    c.cross_cutting_decisions.multi_tenancy.enabled = False
    c.naming_conventions.packages = "br.ufsm.loja.<module_name>"
    conv = validation.convencoes(c)
    assert "tenant_id" not in conv.colunas_base
    assert conv.pacote_base == "br.ufsm.loja."
    assert validation.validar_arquitetura(
        arquitetura(pacote="br.ufsm.loja.users"), spec(), modelo(), conv) == []
    assert validation.validar_arquitetura(arquitetura(), spec(), modelo(), conv) != []


# --------------------------------------------------------- resumo dos outros módulos
def test_resumo_outros_modulos():
    r = templates.resumo_outros_modulos(contexto(), "Users",
                                        {"Catalog": arquitetura()})
    assert "Catalog" in r and "Sales" in r
    assert "- Users" not in r
    assert "Modules that depend on Users: Sales" in r


# --------------------------------------------------------- execução do projeto
def test_gerar_contexto_com_retry(tmp_path):
    chefe = AgenteFalso(contexto(build_order=["Users"]), contexto())
    c = gerar_contexto("loja", SISTEMA, tmp_path, agente=chefe)
    assert c.build_order == DECLARADOS
    assert "does not include" in chefe.chamadas[1]
    assert (tmp_path / "0_contexto.json").exists()
    assert (tmp_path / "contexto.md").exists()


def test_gerar_contexto_sem_modulos(tmp_path):
    with pytest.raises(PipelineError):
        gerar_contexto("x", "sem seção de módulos", tmp_path, agente=AgenteFalso(None))


def test_processar_projeto(tmp_path):
    usados = []

    def fabrica(ctx):
        ags = agentes()
        usados.append(ags)
        return ags

    res = processar_projeto("Loja", SISTEMA, agente_chefe=AgenteFalso(contexto()),
                            fabrica_agentes=fabrica, saida_dir=str(tmp_path))
    pasta = tmp_path / "loja"
    assert list(res["modulos"]) == DECLARADOS
    assert all(s["status"] == "ok" for s in res["modulos"].values())
    for m in ("users", "catalog", "sales"):
        assert (pasta / m / "4_arquitetura.json").exists()
    resumo = json.loads((pasta / "projeto.json").read_text())
    assert resumo["build_order"] == DECLARADOS
    # Sales recebe os serviços já projetados pelos módulos anteriores
    msg_sales = usados[-1]["requisitos"].chamadas[0]  # [0] é a base
    assert "Modules that depend on" not in msg_sales
    assert "Users (business)" in msg_sales


def test_processar_projeto_falha_nao_interrompe(tmp_path):
    chamadas = {"n": 0}

    def fabrica(ctx):
        chamadas["n"] += 1
        if chamadas["n"] == 2:  # 1ª chamada = base; 2ª = Users, que falha sempre
            return agentes(esc=AgenteFalso(spec(ids=("PROB01",))))
        return agentes()

    res = processar_projeto("Loja", SISTEMA, contexto=contexto(),
                            fabrica_agentes=fabrica, saida_dir=str(tmp_path))
    assert res["modulos"]["Users"]["status"] == "falhou"
    assert res["modulos"]["Sales"]["status"] == "ok"


def test_processar_projeto_apenas(tmp_path):
    res = processar_projeto("Loja", SISTEMA, contexto=contexto(), apenas=["catalog"],
                            fabrica_agentes=lambda c: agentes(),
                            saida_dir=str(tmp_path))
    assert list(res["modulos"]) == ["Catalog"]


def test_apenas_com_implementacao_exige_as_dependencias(tmp_path):
    with pytest.raises(PipelineError, match="Sales usa Catalog, Users"):
        processar_projeto("Loja", SISTEMA, contexto=contexto(), apenas=["Sales"],
                          fabrica_agentes=lambda c: agentes(), saida_dir=str(tmp_path))
    assert not (tmp_path / "loja" / "base").exists()  # parou antes de gastar API


def test_apenas_so_na_especificacao_nao_exige_dependencias(tmp_path):
    res = processar_projeto("Loja", SISTEMA, contexto=contexto(), apenas=["Sales"],
                            ate="arquiteto_software",
                            fabrica_agentes=lambda c: agentes(), saida_dir=str(tmp_path))
    assert res["modulos"]["Sales"]["status"] == "ok"


def test_base_so_com_os_modulos_escolhidos(tmp_path):
    usados = []

    def fabrica(ctx):
        usados.append(agentes())
        return usados[-1]

    res = processar_projeto("Loja", SISTEMA, contexto=contexto(), apenas=["users", "Catalog"],
                            fabrica_agentes=fabrica, saida_dir=str(tmp_path))
    assert list(res["modulos"]) == ["Users", "Catalog"]
    msg_base = next(c for c in usados[0]["planejador"].chamadas if "MODE: BASE PLAN" in c)
    assert "MODULES: Users, Catalog\n" in msg_base
    assert "src/modules/sales/routes" not in msg_base  # o roteador não importa Sales


def test_dependencia_ja_implementada_no_disco_vale(tmp_path):
    """Retomada: Users e Catalog já implementados antes; Sales sozinho pode rodar."""
    processar_projeto("Loja", SISTEMA, contexto=contexto(), apenas=["Users", "Catalog"],
                      fabrica_agentes=lambda c: agentes(), saida_dir=str(tmp_path))
    res = processar_projeto("Loja", SISTEMA, contexto=contexto(), apenas=["Sales"],
                            fabrica_agentes=lambda c: agentes(), saida_dir=str(tmp_path))
    assert res["modulos"]["Sales"]["status"] == "ok"
