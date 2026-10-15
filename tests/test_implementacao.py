"""Planejador, Implementadores, DDL e base — sem chamar a API do Gemini."""
import json
import sqlite3
from pathlib import Path

import pytest

import implementacao
import layout
import validation
from ddl import gerar_ddl
from pipeline import PipelineError, carregar_contexto, processar_modulo
from projeto import processar_projeto
from schemas import (Column, Entity, GeneratedFile, GeneratedFiles, Index,
                     ModuleDataModel, PublicService, Screen)
from tests.fakes import (BASE_JAVA, PKG, AgenteFuncao, arq, conteudo,
                         implementador_falso, planejador_falso, plano,
                         plano_base)
from tests.test_pipeline import (agentes, arquitetura, col,
                                 modelo, spec)
from tests.test_projeto import SISTEMA, contexto

DADOS = Path(__file__).parent / "dados"
MAIN = f"src/main/java/{PKG}"
FRONT = "src/modules/usuarios/"


def ctx_sem_frontend():
    c = carregar_contexto().model_copy(deep=True)
    c.tech_stack.frontend.name = "None"
    return c


def validar_plano(p, c=None, base=None):
    return validation.validar_plano(p, "Usuários", spec(), modelo(), arquitetura(),
                                    c or carregar_contexto(),
                                    plano_base() if base is None else base)


def _executar_sqlite(ddl: str) -> None:
    """Checagem de sintaxe: roda CREATE TABLE/INDEX no SQLite (sem os ALTER
    TABLE de FK, que o SQLite não suporta)."""
    sql = "\n".join(l for l in ddl.splitlines() if not l.startswith("ALTER TABLE"))
    con = sqlite3.connect(":memory:")
    con.executescript(sql)
    con.close()


# ================================================================ DDL
def test_ddl_do_modelo():
    ddl = gerar_ddl(modelo())
    assert "CREATE TABLE users (" in ddl
    assert "    id UUID PRIMARY KEY" in ddl
    assert "    tenant_id UUID NOT NULL" in ddl
    assert ("ALTER TABLE user_roles ADD CONSTRAINT fk_user_roles_user_id "
            "FOREIGN KEY (user_id) REFERENCES users (id);") in ddl
    linha = next(l for l in ddl.splitlines() if "ux_users_tenant_email" in l)
    assert linha.startswith("CREATE UNIQUE INDEX ux_users_tenant_email ON users (tenant_id, email);")
    assert "UNIQUE (tenant_id, user_id, role_id)" in ddl
    _executar_sqlite(ddl)


def test_ddl_where_constraints_e_null_textual():
    m = ModuleDataModel(
        module_name="X", relationships=[], external_references=[],
        integrity_rules=[], assumptions=[], data_model_summary="",
        entities=[Entity(
            table="itens", purpose="p", constraints=["CONSTRAINT uq_x UNIQUE (tenant_id, codigo)"],
            columns=[col("id", pk=True), col("tenant_id"),
                     Column(name="status", sql_type="VARCHAR(20)", required=True,
                            primary_key=False, reference="null",
                            constraints=["DEFAULT 'ATIVO'", "CHECK (status IN ('ATIVO','INATIVO'))"]),
                     col("codigo", "VARCHAR(30)"),
                     col("deleted_at", "TIMESTAMP WITH TIME ZONE", required=False)],
            indexes=[Index(name="ix_ativos", columns=["tenant_id", "codigo"], unique=True,
                           where="deleted_at IS NULL", requirement="FR01"),
                     Index(name="ix_t", columns=["tenant_id"], unique=False,
                           where="null", requirement="FR01")])])
    ddl = gerar_ddl(m)
    assert "status VARCHAR(20) NOT NULL DEFAULT 'ATIVO' CHECK" in ddl
    assert "WHERE deleted_at IS NULL" in ddl
    assert "ix_t ON itens (tenant_id);" in ddl
    assert "REFERENCES" not in ddl
    _executar_sqlite(ddl)


def test_ddl_de_saida_real_do_gemini():
    """Modelo de dados real (módulo Users) gerado pelo Gemini numa execução anterior."""
    m = ModuleDataModel.model_validate_json((DADOS / "modelo_users.json").read_text())
    ddl = gerar_ddl(m)
    assert ddl.count("CREATE TABLE") == len(m.entities)
    assert ddl.count("FOREIGN KEY") == 4
    _executar_sqlite(ddl)


def test_modelo_restricao_que_nao_e_sql():
    m = modelo()
    m.entities[0].constraints.append("email deve ser único por tenant")
    m.entities[0].columns[-1].constraints.append("must be lowercase")
    erros = validation.validar_modelo_dados(m, spec())
    assert any("is not SQL" in e and "users" in e for e in erros)
    assert any("users.email" in e for e in erros)


# ================================================================ layout
def test_layout_modulo():
    lay = layout.layout_modulo(carregar_contexto(), "Usuários", "com.distributor.erp.users")
    assert lay.backend_prefixos == (f"src/main/java/{PKG}/", f"src/test/java/{PKG}/")
    assert lay.frontend_prefixo == FRONT
    assert lay.rotas == FRONT + "routes"
    assert layout.migracao_modulo("Usuários", 3) == \
        "src/main/resources/db/migration/V003__usuarios.sql"


def test_limpar_conteudo_remove_cercas():
    assert layout.limpar_conteudo("```java\nclass A {}\n```") == "class A {}\n"
    assert layout.limpar_conteudo("class A {}") == "class A {}\n"


# ================================================================ plano do módulo
def test_plano_ok():
    assert validar_plano(plano()) == []


def test_plano_tabela_sem_entidade():
    p = plano()
    p.backend_files = [f for f in p.backend_files if not f.path.endswith("Role.java")
                       or f.path.endswith("UserRole.java")]
    erros = validar_plano(p)
    assert any("'entity'" in e and "roles" in e for e in erros)


def test_plano_endpoint_sem_controller_e_endpoint_inventado():
    p = plano()
    ctrl = next(f for f in p.backend_files if f.kind == "controller")
    ctrl.endpoints = ["DELETE /api/v1/users/{id}"]
    erros = validar_plano(p)
    assert any("'controller'" in e and "GET /api/v1/users" in e for e in erros)
    assert any("outside the architecture" in e for e in erros)


def test_plano_caminhos_fora_do_modulo_e_migration():
    p = plano()
    p.backend_files.append(arq("src/main/java/com/outra/Coisa.java", "other"))
    p.backend_files.append(arq("src/main/resources/db/migration/V9__x.sql", "other"))
    p.frontend_files.append(arq("src/modules/outro/X.tsx", "component"))
    p.backend_files.append(arq("../fora.java", "other"))
    erros = validar_plano(p)
    assert any("com/outra/Coisa.java" in e and "outside the module folder" in e for e in erros)
    assert any("do not plan migrations" in e for e in erros)
    assert any("src/modules/outro/X.tsx" in e for e in erros)
    assert any("../fora.java" in e and "invalid path" in e for e in erros)


def test_plano_depends_on_desconhecido_e_ciclo():
    p = plano()
    a, b = p.backend_files[0], p.backend_files[1]
    a.depends_on = [b.path, "src/main/java/Fantasma.java"]
    b.depends_on = [a.path]
    erros = validar_plano(p)
    assert any("Fantasma.java" in e for e in erros)
    assert any("circular" in e for e in erros)


def test_plano_pode_depender_da_base_mas_nao_replanejar():
    assert validar_plano(plano()) == []  # UserService depende de ApiException (base)
    p = plano()
    p.backend_files[0].depends_on = [f"{BASE_JAVA}/shared/ApiException.java"]
    assert validar_plano(p) == []
    erros = validar_plano(p, base=plano_base().model_copy(update={"backend_files": []}))
    assert any("ApiException.java" in e for e in erros)


def test_plano_telas_e_rotas():
    p = plano()
    p.screens.append(Screen(name="Fantasma", route="/users", purpose="x", roles=[],
                            endpoints=["GET /api/v1/users"], requirements=["FR77"],
                            page_file=f"{FRONT}pages/Nada.tsx"))
    p.frontend_files = [f for f in p.frontend_files if f.kind != "api_client"]
    p.frontend_files = [f for f in p.frontend_files if f.kind != "routes"]
    erros = validar_plano(p)
    assert any("Repeated screen routes" in e for e in erros)
    assert any("Nada.tsx" in e for e in erros)
    assert any("'api_client'" in e for e in erros)
    assert any("FR77" in e for e in erros)
    assert any("routes file" in e for e in erros)


def test_plano_servico_publico_e_evento_exportados():
    a = arquitetura()
    a.public_services.append(PublicService(
        interface="UserQueryService", method="get", parameters="UUID id",
        return_type="UserDto", purpose="p", consumer_module="Sales"))
    erros = validation.validar_plano(plano(), "Usuários", spec(), modelo(), a,
                                     carregar_contexto(), plano_base())
    assert any("UserQueryService" in e for e in erros)


def test_servico_publico_com_nome_qualificado_casa_com_export_simples():
    """A arquitetura pode declarar o nome qualificado; o plano exporta o simples."""
    a = arquitetura()
    for metodo in ("findById", "findByIds"):  # duas operações da mesma interface
        a.public_services.append(PublicService(
            interface="com.distributor.erp.users.service.UserService", method=metodo,
            parameters="UUID id", return_type="UserDto", purpose="p",
            consumer_module="Sales"))
    assert validation.validar_plano(plano(), "Usuários", spec(), modelo(), a,
                                    carregar_contexto(), plano_base()) == []
    a.public_services[-1].interface = "com.distributor.erp.users.service.Inexistente"
    a.public_services.append(a.public_services[-1].model_copy())
    erros = validation.validar_plano(plano(), "Usuários", spec(), modelo(), a,
                                     carregar_contexto(), plano_base())
    assert sum("Inexistente" in e for e in erros) == 1  # sem repetir a mensagem


def test_export_qualificado_tambem_vale():
    a = arquitetura()
    a.public_services.append(PublicService(
        interface="UserService", method="get", parameters="UUID id",
        return_type="UserDto", purpose="p", consumer_module="Sales"))
    pl = plano()
    servico = next(f for f in pl.backend_files if f.kind == "service")
    servico.exports = ["com.distributor.erp.users.service.UserService"]
    assert validation.validar_plano(pl, "Usuários", spec(), modelo(), a,
                                    carregar_contexto(), plano_base()) == []


def test_plano_sem_frontend_no_contexto():
    c = ctx_sem_frontend()
    assert validar_plano(plano(com_frontend=False), c, plano_base(False)) == []
    erros = validar_plano(plano(), c, plano_base(False))
    assert any("has no frontend" in e for e in erros)


# ================================================================ plano da base
MODULOS = ["Users", "Catalog"]


def test_plano_base_ok():
    assert validation.validar_plano_base(plano_base(), carregar_contexto(), MODULOS) == []


def test_plano_base_invade_modulo_e_sql():
    p = plano_base()
    p.backend_files.append(arq(f"{BASE_JAVA}/users/Coisa.java", "other"))
    p.frontend_files.append(arq("src/modules/users/routes.tsx", "routes"))
    p.backend_files.append(arq("src/main/resources/db/migration/V1__x.sql", "config"))
    p.conventions = []
    erros = validation.validar_plano_base(p, carregar_contexto(), MODULOS)
    assert any("users/Coisa.java" in e and "module folder" in e for e in erros)
    assert any("src/modules/users/routes.tsx" in e for e in erros)
    assert any("V0__" in e for e in erros)
    assert any("conventions" in e for e in erros)


# ================================================================ lotes
def test_lotes_respeitam_dependencias():
    arquivos = plano().backend_files
    lotes = implementacao.montar_lotes(arquivos, 3)
    assert all(len(l) <= 3 for l in lotes)
    ordem = [f.path for l in lotes for f in l]
    assert sorted(ordem) == sorted(f.path for f in arquivos)
    feitos = set()
    for lote in lotes:
        caminhos = {f.path for f in lote}
        for f in lote:
            deps = {d for d in f.depends_on if d in set(ordem)}
            assert deps <= feitos | caminhos, f.path
        feitos |= caminhos


def test_lotes_com_ciclo_nao_travam():
    a = arq("a.ts", "other", deps=["b.ts"])
    b = arq("b.ts", "other", deps=["a.ts"])
    assert [[f.path for f in l] for l in implementacao.montar_lotes([a, b], 5)] == [["a.ts", "b.ts"]]


# ================================================================ código gerado
def conhecidos(**kw):
    p, base = plano(), plano_base()
    caminhos = {f.path for f in p.backend_files + base.backend_files}
    caminhos |= {f.path for f in p.frontend_files + base.frontend_files}
    k = dict(caminhos=caminhos, pacote_base="com.distributor.erp.",
             tabelas={"users", "roles", "user_roles"},
             endpoints={"/api/v1/users", "/api/v1/users/{}"},
             rotas_modulos={"src/modules/usuarios/routes"})
    k.update(kw)
    return validation.Conhecidos(**k)


def gerar(*arquivos):
    return GeneratedFiles(files=[GeneratedFile(path=f.path, content=conteudo(f))
                                 for f in arquivos], notes=[])


def test_arquivos_ok():
    p = plano()
    todos = p.backend_files + p.frontend_files
    assert validation.validar_arquivos(gerar(*todos), todos, conhecidos()) == []


def test_arquivos_faltando_e_extra():
    p = plano()
    pedidos = p.backend_files[:2]
    g = gerar(p.backend_files[0], p.backend_files[3])
    erros = validation.validar_arquivos(g, pedidos, conhecidos())
    assert any("not delivered" in e and "Role.java" in e for e in erros)
    assert any("not requested" in e for e in erros)


def test_arquivos_java_pacote_classe_import_e_tabela():
    f = arq(f"{MAIN}/domain/User.java", "entity", ["User"], tables=["users"])
    codigo = ("package com.distributor.erp.usuarios;\n"
              "import com.distributor.erp.users.domain.Inexistente;\n"
              "import com.distributor.erp.shared.ApiException;\n"
              "import org.springframework.stereotype.Service;\n"
              '@Table(name = "clientes")\npublic class Usuario {}\n')
    g = GeneratedFiles(files=[GeneratedFile(path=f.path, content=codigo)], notes=[])
    erros = validation.validar_arquivos(g, [f], conhecidos())
    assert any("package com.distributor.erp.users.domain" in e for e in erros)
    assert any("'User'" in e for e in erros)
    assert any("Inexistente" in e for e in erros)
    assert any("clientes" in e for e in erros)
    assert not any("ApiException" in e or "springframework" in e for e in erros)


def test_arquivos_import_estatico_aninhado_e_curinga():
    f = arq(f"{MAIN}/domain/User.java", "entity", ["User"])
    codigo = ("package com.distributor.erp.users.domain;\n"
              "import static com.distributor.erp.shared.ApiException.notFound;\n"
              "import com.distributor.erp.users.domain.Role.Kind;\n"
              "import com.distributor.erp.users.repository.*;\n"
              "public class User {}\n")
    g = GeneratedFiles(files=[GeneratedFile(path=f.path, content=codigo)], notes=[])
    assert validation.validar_arquivos(g, [f], conhecidos()) == []


def test_arquivos_placeholder():
    f = plano().backend_files[0]
    g = gerar(f)
    g.files[0].content += "// TODO implementar\n"
    assert any("incomplete" in e for e in validation.validar_arquivos(g, [f], conhecidos()))


def test_arquivos_front_import_e_endpoint():
    f = arq(f"{FRONT}pages/UsersPage.tsx", "page", ["UsersPage"])
    codigo = ("import React from 'react';\n"
              "import { http } from '@/api/http';\n"
              "import { listUsers } from '../api/usersApi';\n"
              "import { nada } from '../api/naoExiste';\n"
              "const a = `/api/v1/users/${id}`;\n"
              "const b = '/api/v1/clientes';\n"
              "const base = '/api/v1';\n"
              "export const UsersPage = () => null;\n")
    g = GeneratedFiles(files=[GeneratedFile(path=f.path, content=codigo)], notes=[])
    erros = validation.validar_arquivos(g, [f], conhecidos())
    assert len(erros) == 2, erros
    assert any("naoExiste" in e for e in erros)
    assert any("/api/v1/clientes" in e for e in erros)


def test_arquivos_front_endpoint_de_outro_modulo():
    f = arq(f"{FRONT}api/x.ts", "api_client")
    g = GeneratedFiles(files=[GeneratedFile(
        path=f.path, content="export const u = '/api/v1/customers/:id';\n")], notes=[])
    assert validation.validar_arquivos(g, [f], conhecidos()) != []
    k = conhecidos(endpoints={"/api/v1/customers/{}"})
    assert validation.validar_arquivos(g, [f], k) == []


def test_arquivos_base_importa_rotas_de_modulo():
    f = arq("src/App.tsx", "component", ["App"])
    g = GeneratedFiles(files=[GeneratedFile(
        path=f.path, content="import r from './modules/usuarios/routes';\n"
                             "import s from './modules/vendas/routes';\nexport const App = r;\n")],
        notes=[])
    erros = validation.validar_arquivos(g, [f], conhecidos())
    assert len(erros) == 1 and "vendas" in erros[0]


# ================================================================ pipeline do módulo
def test_pipeline_gera_codigo(tmp_path):
    ags = agentes()
    res = processar_modulo("Usuários", "desc", agentes=ags, saida_dir=str(tmp_path))
    pasta = tmp_path / "usuarios"
    back, front = pasta / "codigo" / "backend", pasta / "codigo" / "frontend"

    # base
    assert (back / "pom.xml").exists() and (front / "src/main.tsx").exists()
    assert (pasta / "base" / "5_plano.json").exists()
    # migration gerada por código
    mig = back / "src/main/resources/db/migration/V001__usuarios.sql"
    assert "CREATE TABLE user_roles" in mig.read_text()
    # backend e frontend do módulo
    user = (back / MAIN / "domain/User.java").read_text()
    assert user.startswith("package com.distributor.erp.users.domain;")
    assert '@Table(name = "users")' in user
    assert (front / FRONT / "routes.tsx").exists()
    # artefatos
    for f in ("5_plano.json", "6_backend.json", "7_frontend.json", "relatorio.md"):
        assert (pasta / f).exists(), f
    manifesto = json.loads((pasta / "6_backend.json").read_text())
    assert manifesto[0]["gerado_por"] == "ddl.py"
    assert "Plano de implementação" in (pasta / "relatorio.md").read_text()
    assert res["metricas"]["implementador_backend"]["lotes"] == 2

    # o planejador recebe a DDL; o 2º lote recebe o código de que depende
    assert "CREATE TABLE users" in ags["planejador"].chamadas[-1]
    lote2 = ags["implementador_backend"].chamadas[-1]
    assert "batch 2/2" in lote2
    assert f"=== {MAIN}/service/UserService.java ===" in lote2


def test_implementador_retry(tmp_path):
    back = implementador_falso(sabotar=lambda arqs: arqs.pop(),  # 1º lote do módulo sem 1 arquivo
                               quando=lambda msg: "MODE: MODULE" in msg)
    res = processar_modulo("Usuários", "d", agentes=agentes(back=back),
                           saida_dir=str(tmp_path))
    m = res["metricas"]["implementador_backend"]
    assert m["tentativas"] == m["lotes"] + 1
    assert any("REPROVADA" in c and "not delivered" in c for c in back.chamadas)


def test_implementador_falha_definitiva(tmp_path):
    def estraga(arqs):
        arqs[0].content = "class Errada {}"
    back = implementador_falso(sabotar=estraga, vezes=99,
                               quando=lambda msg: "MODE: MODULE" in msg)
    with pytest.raises(PipelineError):
        processar_modulo("Usuários", "d", agentes=agentes(back=back),
                         saida_dir=str(tmp_path), max_tentativas=2)
    pasta = tmp_path / "usuarios"
    assert list(pasta.glob("implementador_backend.lote1.invalido.json"))
    assert not (pasta / "codigo/backend" / MAIN / "domain/User.java").exists()


def test_planejador_reprovado_falha(tmp_path):
    ruim = plano()
    ruim.backend_files = ruim.backend_files[:1]
    with pytest.raises(PipelineError):
        processar_modulo("Usuários", "d", saida_dir=str(tmp_path), max_tentativas=1,
                         agentes=agentes(plan=AgenteFuncao(
                             lambda m: plano_base() if "BASE PLAN" in m else ruim)))
    assert (tmp_path / "usuarios" / "planejador.invalido.json").exists()


def test_retomar_de_planejador_sem_refazer_especificacao(tmp_path):
    processar_modulo("Usuários", "d", ate="arquiteto_software", agentes=agentes(),
                     saida_dir=str(tmp_path))
    def proibido(msg):
        raise AssertionError("etapa de especificação não deveria rodar")
    ags = agentes()
    for e in ("requisitos", "esclarecedor", "modelador_dados", "arquiteto_software"):
        ags[e] = AgenteFuncao(proibido)
    res = processar_modulo("Usuários", "d", de="planejador", agentes=ags,
                           saida_dir=str(tmp_path))
    assert "plano" in res and res["arquivos_frontend"]
    rel = (tmp_path / "usuarios" / "relatorio.md").read_text()
    assert "Análise do módulo" in rel and "Código backend" in rel


def test_retomar_sem_artefatos_falha(tmp_path):
    with pytest.raises(PipelineError, match="2_especificacao.json"):
        processar_modulo("Usuários", "d", de="planejador", agentes=agentes(),
                         saida_dir=str(tmp_path))


def test_ate_planejador_nao_implementa(tmp_path):
    ags = agentes()
    processar_modulo("Usuários", "d", ate="planejador", agentes=ags,
                     saida_dir=str(tmp_path))
    assert (tmp_path / "usuarios" / "5_plano.json").exists()
    assert not (tmp_path / "usuarios" / "codigo").exists()
    assert ags["implementador_backend"].chamadas == []


def test_sem_frontend(tmp_path):
    ags = agentes(plan=planejador_falso(com_frontend=False))
    res = processar_modulo("Usuários", "d", agentes=ags, saida_dir=str(tmp_path),
                           contexto=ctx_sem_frontend())
    assert "arquivos_frontend" not in res
    assert ags["implementador_frontend"].chamadas == []
    assert not (tmp_path / "usuarios" / "codigo" / "frontend").exists()


def test_base_reaproveitada(tmp_path):
    processar_modulo("Usuários", "d", agentes=agentes(), saida_dir=str(tmp_path))
    ags = agentes()
    processar_modulo("Usuários", "d", de="planejador", agentes=ags,
                     saida_dir=str(tmp_path))
    assert not any("MODE: BASE PLAN" in c for c in ags["planejador"].chamadas)
    assert not any("MODE: BASE" in c for c in ags["implementador_backend"].chamadas)


# ================================================================ projeto
def test_projeto_gera_codigo_compartilhado(tmp_path):
    usados = []

    def fabrica(ctx):
        usados.append(agentes())
        return usados[-1]

    res = processar_projeto("Loja", SISTEMA, contexto=contexto(),
                            fabrica_agentes=fabrica, saida_dir=str(tmp_path))
    assert all(s["status"] == "ok" for s in res["modulos"].values())
    codigo = tmp_path / "loja" / "codigo"
    migs = sorted(p.name for p in (codigo / "backend/src/main/resources/db/migration").iterdir())
    assert migs == ["V001__users.sql", "V002__catalog.sql", "V003__sales.sql"]
    for m in ("users", "catalog", "sales"):
        assert (codigo / "frontend/src/modules" / m / "routes.tsx").exists()
    # base gerada uma vez, com a lista de rotas de todos os módulos
    assert sum("MODE: BASE PLAN" in c for u in usados for c in u["planejador"].chamadas) == 1
    msg_base = next(c for c in usados[0]["implementador_frontend"].chamadas)
    assert "src/modules/sales/routes" in msg_base
    assert json.loads((tmp_path / "loja" / "projeto.json").read_text())["codigo"]


def test_projeto_so_especificacao_nao_cria_base(tmp_path):
    processar_projeto("Loja", SISTEMA, contexto=contexto(), ate="arquiteto_software",
                      fabrica_agentes=lambda c: agentes(), saida_dir=str(tmp_path))
    assert not (tmp_path / "loja" / "base").exists()
    assert not (tmp_path / "loja" / "codigo").exists()


def test_base_reprovada(tmp_path):
    back = implementador_falso(sabotar=lambda arqs: arqs.pop(), vezes=99,
                               quando=lambda msg: "MODE: BASE" in msg)
    with pytest.raises(PipelineError, match="implementador_backend_base"):
        processar_modulo("Usuários", "d", agentes=agentes(back=back),
                         saida_dir=str(tmp_path), max_tentativas=2)
    assert (tmp_path / "usuarios/base/implementador_backend_base.lote1.invalido.json").exists()
    # métricas preservadas mesmo com a falha (custo das etapas que rodaram)
    m = json.loads((tmp_path / "usuarios/metricas.json").read_text())
    assert m["arquiteto_software"]["tentativas"] == 1
    mb = json.loads((tmp_path / "usuarios/base/metricas.json").read_text())
    assert mb["implementador_backend_base"]["tentativas"] == 2


# ================================================================ reparo de caminho
def test_reparo_saida_real_do_gemini():
    """Lote 6 da base na 1ª execução real: o Gemini escreveu OutboxPublisher com o
    path de OutboxProcessorService (duplicado) e deixou OutboxPublisher faltando."""
    from schemas import ImplementationPlan
    p = ImplementationPlan.model_validate_json((DADOS / "plano_base_real.json").read_text())
    lote = implementacao.montar_lotes(p.backend_files)[5]
    g = GeneratedFiles.model_validate_json((DADOS / "lote_caminho_trocado.json").read_text())
    k = validation.Conhecidos(caminhos={f.path for f in p.backend_files},
                              pacote_base="com.distributor.erp.")
    antes = validation.validar_arquivos(g, lote, k)
    assert any("not delivered" in e for e in antes)

    implementacao.reparar_caminhos(g, lote)
    assert validation.validar_arquivos(g, lote, k) == []
    assert any("OutboxPublisher.java" in n for n in g.notes)


def _java(pacote, tipo):
    return f"package {pacote};\n\npublic class {tipo} {{\n}}\n"


def test_reparo_so_quando_inequivoco():
    a = arq(f"{MAIN}/service/A.java", "service", ["A"])
    b = arq(f"{MAIN}/service/B.java", "service", ["B"])
    pkg = "com.distributor.erp.users.service"
    # B escrito com o path de A: reparado
    g = GeneratedFiles(files=[GeneratedFile(path=a.path, content=_java(pkg, "A")),
                              GeneratedFile(path=a.path, content=_java(pkg, "B"))], notes=[])
    implementacao.reparar_caminhos(g, [a, b])
    assert [f.path for f in g.files] == [a.path, b.path]
    # B em outro pacote: não reparado (fica para a validação reprovar)
    g = GeneratedFiles(files=[GeneratedFile(path=a.path, content=_java(pkg, "A")),
                              GeneratedFile(path=a.path, content=_java("x.y", "B"))], notes=[])
    implementacao.reparar_caminhos(g, [a, b])
    assert [f.path for f in g.files] == [a.path, a.path] and g.notes == []
    # nada faltando: nada muda
    g = GeneratedFiles(files=[GeneratedFile(path=a.path, content=_java(pkg, "A")),
                              GeneratedFile(path=b.path, content=_java(pkg, "B"))], notes=[])
    implementacao.reparar_caminhos(g, [a, b])
    assert g.notes == []


def test_reparo_no_pipeline_evita_nova_tentativa(tmp_path):
    def troca(arqs):  # 2º arquivo Java do lote vai com o path do 1º
        java = [f for f in arqs if f.path.endswith(".java")]
        java[1].path = java[0].path
    back = implementador_falso(sabotar=troca, quando=lambda m: "MODE: MODULE" in m)
    res = processar_modulo("Usuários", "d", agentes=agentes(back=back),
                           saida_dir=str(tmp_path))
    m = res["metricas"]["implementador_backend"]
    assert m["tentativas"] == m["lotes"]  # nenhuma nova chamada ao modelo
    notas = [n for x in res["arquivos_backend"] for n in x.get("notas", [])]
    assert any("Caminho corrigido" in n for n in notas)


# ================================================================ retomada por arquivo
def test_retomada_gera_so_o_que_falta(tmp_path):
    def estraga(arqs):
        arqs.pop()
    # lote 2 do módulo falha sempre na 1ª execução
    ruim = implementador_falso(sabotar=estraga, vezes=99,
                               quando=lambda m: "MODE: MODULE" in m and "batch 2/2" in m)
    with pytest.raises(PipelineError):
        processar_modulo("Usuários", "d", agentes=agentes(back=ruim),
                         saida_dir=str(tmp_path), max_tentativas=2)
    pasta = tmp_path / "usuarios"
    progresso = pasta / "implementador_backend.progresso.json"
    feitos = [m["path"] for m in json.loads(progresso.read_text()) if "path" in m]
    assert len(feitos) == 5  # lote 1 (a migration é regenerada em código)

    bom = implementador_falso()
    res = processar_modulo("Usuários", "d", de="planejador", agentes=agentes(back=bom),
                           saida_dir=str(tmp_path))
    # 2ª execução: o planejador refaz o plano (igual), mas só o lote pendente é gerado
    modulo = [c for c in bom.chamadas if "MODE: MODULE" in c]
    assert len(modulo) == 1 and "batch 1/1" in modulo[0]
    pedidos_2 = {f["path"] for f in json.loads(modulo[0].split("(JSON):\n")[-1])}
    assert pedidos_2 == {f"{MAIN}/api/UserController.java",
                         f"src/test/java/{PKG}/service/UserServiceTest.java"}
    assert not progresso.exists()
    gerados = {m["path"] for m in res["arquivos_backend"] if "path" in m}
    assert f"{MAIN}/domain/User.java" in gerados and f"{MAIN}/api/UserController.java" in gerados


# ================================================================ falsos positivos
def test_todo_em_portugues_nao_e_placeholder():
    f = arq(f"{FRONT}pages/UsersPage.tsx", "page", ["UsersPage"])
    texto = ("export const UsersPage = () => 'Selecione todo o período';\n"
             "// valida todo o formulário\n")
    g = GeneratedFiles(files=[GeneratedFile(path=f.path, content=texto)], notes=[])
    assert validation.validar_arquivos(g, [f], conhecidos()) == []
    g.files[0].content += "// TODO\n"
    assert validation.validar_arquivos(g, [f], conhecidos()) != []


def test_package_info_nao_exige_tipo():
    f = arq(f"{MAIN}/domain/package-info.java", "other")
    g = GeneratedFiles(files=[GeneratedFile(
        path=f.path, content="package com.distributor.erp.users.domain;\n")], notes=[])
    assert validation.validar_arquivos(g, [f], conhecidos()) == []


# ================================================================ endpoints na base
def test_base_sem_arquiteturas_nao_checa_urls():
    f = arq("src/context/AuthContext.tsx", "store", ["AuthProvider"])
    g = GeneratedFiles(files=[GeneratedFile(
        path=f.path, content="export const AuthProvider = '/api/v1/auth/login';\n")], notes=[])
    assert validation.validar_arquivos(g, [f], conhecidos(endpoints=None)) == []
    assert validation.validar_arquivos(g, [f], conhecidos()) != []


def test_base_recebe_endpoints_do_modulo(tmp_path):
    ags = agentes()
    processar_modulo("Usuários", "d", agentes=ags, saida_dir=str(tmp_path))
    base_front = [c for c in ags["implementador_frontend"].chamadas if "MODE: BASE" in c]
    assert base_front and all("KNOWN API ENDPOINTS" in c and '"/api/v1/users"' in c
                              for c in base_front)


def test_reparo_frontend_pelos_exports():
    """Padrão visto na 2ª execução real: UserListPage entregue com o path de UserFormPage."""
    lista = arq(f"{FRONT}pages/UserListPage.tsx", "page", ["UserListPage"])
    form = arq(f"{FRONT}pages/UserFormPage.tsx", "page", ["UserFormPage"])
    g = GeneratedFiles(files=[
        GeneratedFile(path=form.path, content="export default function UserFormPage() {}\n"),
        GeneratedFile(path=form.path, content="export const UserListPage = () => null;\n"),
    ], notes=[])
    implementacao.reparar_caminhos(g, [lista, form])
    assert [f.path for f in g.files] == [form.path, lista.path]
    assert validation.validar_arquivos(g, [lista, form], conhecidos()) == []


def test_reparo_frontend_ambiguo_nao_mexe():
    a = arq(f"{FRONT}a.ts", "types", ["X"])
    b = arq(f"{FRONT}b.ts", "types", ["X"])  # mesmos exports: ambíguo
    c = arq(f"{FRONT}c.ts", "types", ["Y"])
    g = GeneratedFiles(files=[GeneratedFile(path=c.path, content="export const Y = 1;\n"),
                              GeneratedFile(path=c.path, content="export const X = 1;\n")],
                       notes=[])
    implementacao.reparar_caminhos(g, [a, b, c])
    assert [f.path for f in g.files] == [c.path, c.path] and g.notes == []


# ================================================================ assinaturas no contexto
from assinaturas import assinaturas  # noqa: E402

JAVA_REPO = """package com.x.repo;

import java.util.UUID;

public interface RoleRepository extends JpaRepository<Role, UUID> {
    Optional<Role> findByTenantIdAndCode(UUID tenantId, String code);
    List<Role> findAllByTenantIdAndIdIn(UUID tenantId, Collection<UUID> ids);
}
"""
JAVA_CLASSE = """package com.x.error;

public class BusinessException extends RuntimeException {
    private final String code;
    public static final int LIMITE = 5;

    public BusinessException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String getCode() {
        return code;
    }

    private void interno() {
        int x = 1;
    }
}
"""
TSX = """import React from 'react';

interface Props {
  requiredRoles?: string[];
}

export const ProtectedRoute: React.FC<Props> = ({ requiredRoles }) => {
  const ok = true;
  return null;
};

export function soma(a: number, b: number): number {
  return a + b;
}
"""


def test_assinaturas_java():
    r = assinaturas("R.java", JAVA_REPO)
    assert "findAllByTenantIdAndIdIn(UUID tenantId, Collection<UUID> ids);" in r
    c = assinaturas("B.java", JAVA_CLASSE)
    assert "public BusinessException(String code, String message) { ... }" in c
    assert "public static final int LIMITE = 5;" in c
    assert "super(message)" not in c and "interno" not in c and "private" not in c


def test_assinaturas_tsx():
    r = assinaturas("P.tsx", TSX)
    assert "requiredRoles?: string[];" in r
    assert "export const ProtectedRoute: React.FC<Props> = ({ requiredRoles }) => ..." in r
    assert "export function soma(a: number, b: number): number { ... }" in r
    assert "const ok" not in r and "return a + b" not in r


def test_dependencias_acima_do_limite_vao_como_assinaturas(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a/RoleRepository.java").write_text(JAVA_REPO)
    (tmp_path / "a/BusinessException.java").write_text(JAVA_CLASSE)
    f = arq("a/Servico.java", "service",
            deps=["a/BusinessException.java", "a/RoleRepository.java"])
    txt = implementacao.conteudo_dependencias(tmp_path, [f], limite=len(JAVA_CLASSE) + 40)
    assert "super(message)" in txt                       # 1º inteiro
    assert "só assinaturas" in txt                        # 2º resumido, não omitido
    assert "findAllByTenantIdAndIdIn" in txt


def test_lotes_do_modulo_recebem_api_da_base(tmp_path):
    ags = agentes()
    processar_modulo("Usuários", "d", agentes=ags, saida_dir=str(tmp_path))
    back = [c for c in ags["implementador_backend"].chamadas if "MODE: MODULE" in c]
    front = [c for c in ags["implementador_frontend"].chamadas if "MODE: MODULE" in c]
    assert all("BASE API" in c and "public class ApiException { ... }" in c for c in back)
    assert all("BASE API" in c and "export const http = 1;" in c for c in front)


def test_reparo_deslocamento_em_cadeia():
    """Padrão da regeneração real (lote 7): cada arquivo com o conteúdo do vizinho."""
    pkg = "com.distributor.erp.users.api.controller"
    nomes = ["AuthController", "UserController", "RoleController"]
    pedidos = [arq(f"{MAIN}/api/controller/{n}.java", "controller", [n]) for n in nomes]
    g = GeneratedFiles(files=[
        GeneratedFile(path=pedidos[0].path, content=_java(pkg, "UserController")),
        GeneratedFile(path=pedidos[1].path, content=_java(pkg, "RoleController")),
        GeneratedFile(path=pedidos[2].path, content=_java(pkg, "AuthController")),
    ], notes=[])
    implementacao.reparar_caminhos(g, pedidos)
    assert sorted(f.path for f in g.files) == sorted(f.path for f in pedidos)
    for f in g.files:
        assert f"class {f.path.rsplit('/', 1)[1][:-5]} " in f.content
    assert validation.validar_arquivos(g, pedidos, conhecidos()) == []


def test_reparo_tipo_repetido_nao_mexe():
    pkg = "com.distributor.erp.users.service"
    a = arq(f"{MAIN}/service/A.java", "service", ["A"])
    b = arq(f"{MAIN}/service/B.java", "service", ["B"])
    g = GeneratedFiles(files=[GeneratedFile(path=a.path, content=_java(pkg, "B")),
                              GeneratedFile(path=b.path, content=_java(pkg, "B"))], notes=[])
    implementacao.reparar_caminhos(g, [a, b])
    assert [f.path for f in g.files] == [a.path, b.path] and g.notes == []
