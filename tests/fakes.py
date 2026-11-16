"""Agentes falsos e fábricas de artefatos, compartilhados pelos testes.

Nada aqui chama a API do modelo: os agentes falsos devolvem objetos prontos.
"""
import json
import posixpath
import re
from typing import Callable, List, Optional

from mas.agentes.mensagens import MARCADOR_LOTE
from mas.codigo import layout
from mas.pipeline.modulo import carregar_contexto
from mas.schemas import (ArchitecturalDecision, Column, Endpoint, Entity,
                         FinalSpecification, GeneratedFile, GeneratedFiles,
                         ImplementationPlan, Index, ModuleAnalysis,
                         ModuleArchitecture, ModuleDataModel, PlannedFile,
                         Problem, Relationship, Requirement, ResolvedProblem,
                         Screen)

PKG = "com/distributor/erp/users"
BASE_JAVA = "src/main/java/com/distributor/erp"


# --------------------------------------------------------------- agentes falsos
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


class AgenteFuncao:
    """Responde com fn(mensagem); guarda as mensagens recebidas."""

    def __init__(self, fn: Callable[[str], object]):
        self.fn = fn
        self.chamadas: List[str] = []

    def invoke(self, entrada, config=None):
        msg = entrada["messages"][0]["content"]
        self.chamadas.append(msg)
        return {"structured_response": self.fn(msg), "messages": []}


# --------------------------------------------------------------- especificação (1–4)
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


# --------------------------------------------------------------- projeto
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


# --------------------------------------------------------------- planos
def arq(path, kind, exports=(), deps=(), tables=(), endpoints=(), reqs=()):
    return PlannedFile(path=path, kind=kind, purpose=f"{kind} {path}",
                       exports=list(exports), depends_on=list(deps),
                       tables=list(tables), endpoints=list(endpoints),
                       requirements=list(reqs))


def plano_base(com_frontend=True) -> ImplementationPlan:
    front = [
        arq("package.json", "build"),
        arq("src/api/http.ts", "api_client", ["http"]),
        arq("src/App.tsx", "component", ["App"], ["src/api/http.ts"]),
        arq("src/main.tsx", "entrypoint", [], ["src/App.tsx"]),
    ] if com_frontend else []
    return ImplementationPlan(
        module_name="base",
        backend_files=[
            arq("pom.xml", "build"),
            arq(f"{BASE_JAVA}/Application.java", "entrypoint", ["Application"]),
            arq(f"{BASE_JAVA}/shared/ApiException.java", "exception", ["ApiException"]),
        ],
        frontend_files=front, screens=[],
        conventions=["Cada módulo exporta por padrão um array de rotas em routes.tsx."],
        implementation_notes=[])


def plano(front="src/modules/usuarios/", pkg=PKG, com_frontend=True) -> ImplementationPlan:
    main, test = f"src/main/java/{pkg}", f"src/test/java/{pkg}"
    servico = f"{main}/service/UserService.java"
    backend = [
        arq(f"{main}/domain/User.java", "entity", ["User"], tables=["users"]),
        arq(f"{main}/domain/Role.java", "entity", ["Role"], tables=["roles"]),
        arq(f"{main}/domain/UserRole.java", "entity", ["UserRole"],
            [f"{main}/domain/User.java", f"{main}/domain/Role.java"], ["user_roles"]),
        arq(f"{main}/repository/UserRepository.java", "repository", ["UserRepository"],
            [f"{main}/domain/User.java"], ["users"]),
        arq(servico, "service", ["UserService"],
            [f"{main}/repository/UserRepository.java",
             f"{BASE_JAVA}/shared/ApiException.java"], reqs=["FR01"]),
        arq(f"{main}/api/UserController.java", "controller", ["UserController"],
            [servico], endpoints=["GET /api/v1/users"], reqs=["FR01"]),
        arq(f"{test}/service/UserServiceTest.java", "test", ["UserServiceTest"], [servico]),
    ]
    frontend, telas = [], []
    if com_frontend:
        cliente, pagina = f"{front}api/usersApi.ts", f"{front}pages/UsersPage.tsx"
        frontend = [
            arq(cliente, "api_client", ["listUsers"], ["src/api/http.ts"],
                endpoints=["GET /api/v1/users"]),
            arq(pagina, "page", ["UsersPage"], [cliente], reqs=["FR01"]),
            arq(f"{front}routes.tsx", "routes", ["routes"], [pagina]),
        ]
        telas = [Screen(name="Usuários", route="/users", purpose="listar",
                        roles=["ADMIN"], endpoints=["GET /api/v1/users"],
                        requirements=["FR01"], page_file=pagina)]
    return ImplementationPlan(
        module_name="Users", backend_files=backend, frontend_files=frontend,
        screens=telas, conventions=[], implementation_notes=[])


def planejador_falso(**kw) -> AgenteFuncao:
    """Devolve o plano da base ou o do módulo, conforme o modo da mensagem."""
    def fn(msg):
        if "MODE: BASE PLAN" in msg:
            return plano_base(kw.get("com_frontend", True))
        m = re.search(r"- frontend module files: (\S+)", msg)
        front = m.group(1) if m else "src/modules/usuarios/"
        return plano(front=front, **kw)
    return AgenteFuncao(fn)


# --------------------------------------------------------------- implementador
def pedidos(msg: str) -> List[PlannedFile]:
    # no retry, os erros de validação vêm depois do JSON: lê só o JSON
    texto = msg.split(MARCADOR_LOTE, 1)[1].lstrip()
    dados, _ = json.JSONDecoder().raw_decode(texto)
    return [PlannedFile(**d) for d in dados]


def _relativo(origem: str, destino: str) -> str:
    r = posixpath.relpath(layout.sem_extensao(destino), posixpath.dirname(origem))
    return r if r.startswith(".") else "./" + r


def conteudo(f: PlannedFile) -> str:
    """Código mínimo e coerente com o plano (passa nos validadores)."""
    nome = posixpath.splitext(posixpath.basename(f.path))[0]
    if f.path.endswith(".java"):
        fq = layout.fqcn(f.path)
        linhas = [f"package {fq.rpartition('.')[0]};", ""]
        linhas += [f"import {layout.fqcn(d)};" for d in f.depends_on
                   if d.endswith(".java") and layout.fqcn(d)]
        linhas += [f'@Table(name = "{t}")' for t in f.tables if f.kind == "entity"]
        linhas.append(f"public class {nome} {{\n}}")
        return "\n".join(linhas) + "\n"
    if f.path.endswith((".ts", ".tsx")):
        linhas = [f"import * as dep{i} from '{_relativo(f.path, d)}';"
                  for i, d in enumerate(f.depends_on)]
        linhas += [f"export const url{i} = '{e.split()[1]}';"
                   for i, e in enumerate(f.endpoints)]
        linhas += [f"export const {x} = 1;" for x in f.exports]
        return "\n".join(linhas) + "\n"
    return "{}\n"


def implementador_falso(sabotar: Optional[Callable[[List[GeneratedFile]], None]] = None,
                        vezes: int = 1,
                        quando: Callable[[str], bool] = lambda msg: True) -> AgenteFuncao:
    """Gera os arquivos pedidos; `sabotar` estraga as `vezes` primeiras respostas
    cujas mensagens satisfazem `quando` (ex.: só lotes do módulo, não da base)."""
    estado = {"n": 0}

    def fn(msg):
        arquivos = [GeneratedFile(path=f.path, content=conteudo(f)) for f in pedidos(msg)]
        if sabotar and estado["n"] < vezes and quando(msg):
            estado["n"] += 1
            sabotar(arquivos)
        return GeneratedFiles(files=arquivos, notes=[])
    return AgenteFuncao(fn)


# --------------------------------------------------------------- pipeline completo
def agentes(esc=None, mod=None, arq=None, plan=None, back=None, front=None):
    return {"requisitos": AgenteFalso(analise()),
            "esclarecedor": esc or AgenteFalso(spec()),
            "modelador_dados": mod or AgenteFalso(modelo()),
            "arquiteto_software": arq or AgenteFalso(arquitetura()),
            "planejador": plan or planejador_falso(),
            "implementador_backend": back or implementador_falso(),
            "implementador_frontend": front or implementador_falso()}
