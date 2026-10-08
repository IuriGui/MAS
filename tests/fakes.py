"""Agentes falsos e fábricas de planos para testar sem chamar a API do Gemini."""
import json
import posixpath
import re
from typing import Callable, List, Optional

import layout
from schemas import (GeneratedFile, GeneratedFiles, ImplementationPlan,
                     PlannedFile, Screen)
from system_prompts.templates import MARCADOR_LOTE

PKG = "com/distributor/erp/users"
BASE_JAVA = "src/main/java/com/distributor/erp"


class AgenteFuncao:
    """Responde com fn(mensagem); guarda as mensagens recebidas."""

    def __init__(self, fn: Callable[[str], object]):
        self.fn = fn
        self.chamadas: List[str] = []

    def invoke(self, entrada, config=None):
        msg = entrada["messages"][0]["content"]
        self.chamadas.append(msg)
        return {"structured_response": self.fn(msg), "messages": []}


def arq(path, kind, exports=(), deps=(), tables=(), endpoints=(), reqs=()):
    return PlannedFile(path=path, kind=kind, purpose=f"{kind} {path}",
                       exports=list(exports), depends_on=list(deps),
                       tables=list(tables), endpoints=list(endpoints),
                       requirements=list(reqs))


# --------------------------------------------------------------- planos
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
