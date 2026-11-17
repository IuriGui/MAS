"""Builds the USER MESSAGE for each agent.

The system prompt remains static (role + rules); the module's variable data
is provided here. Previously, the {{...}} placeholders were inside the system
prompt and were never replaced.
"""
import json
import re
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

from mas.codigo.layout import PREFIXO_MIGRACAO_BASE, Layout
from mas.schemas import (FinalSpecification, ImplementationPlan, ModuleAnalysis,
                         ModuleArchitecture, ModuleDataModel, PlannedFile,
                         ProjectContext)

NO_CONTEXT = "(no other module information provided)"


def _mesmo(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def msg_arquiteto_chefe(descricao_sistema: str, modulos: List[Tuple[str, str]]) -> str:
    nomes = ", ".join(n for n, _ in modulos)
    return (
        f"{descricao_sistema.strip()}\n\n"
        f"DECLARED MODULES (use these exact names in module_map and build_order): {nomes}"
    )


def resumo_outros_modulos(
    contexto: ProjectContext,
    nome: str,
    arquiteturas: Optional[Dict[str, ModuleArchitecture]] = None,
) -> str:
    """Resumo dos OUTROS módulos para o agente de um módulo.

    Usa o module_map do Arquiteto-chefe e, para módulos já processados, os
    serviços públicos e eventos que eles de fato expõem.
    """
    arquiteturas = arquiteturas or {}
    linhas = []
    for m in contexto.module_map:
        if _mesmo(m.name, nome):
            continue
        linhas.append(f"- {m.name} ({m.kind}): {m.responsibility}")
        if m.depends_on:
            linhas.append(f"  Depends on: {', '.join(m.depends_on)}")
        if m.exposed_capabilities:
            linhas.append(f"  Exposes: {'; '.join(m.exposed_capabilities)}")
        arq = next((a for n, a in arquiteturas.items() if _mesmo(n, m.name)), None)
        if arq:
            for s in arq.public_services:
                linhas.append(f"  Public service (already designed): "
                              f"{s.interface}.{s.method}({s.parameters}) -> {s.return_type}")
            for e in arq.domain_events:
                linhas.append(f"  Domain event (already designed): {e.name} {{{e.payload}}}")
    consumidores = [m.name for m in contexto.module_map
                    if any(_mesmo(d, nome) for d in m.depends_on)]
    if consumidores:
        linhas.append(f"\nModules that depend on {nome}: {', '.join(consumidores)}")
    return "\n".join(linhas) if linhas else NO_CONTEXT


def msg_requisitos(nome: str, descricao: str, outros_modulos: str = "") -> str:
    return (
        f"MODULE: {nome}\n\n"
        f"MODULE DESCRIPTION:\n{descricao}\n\n"
        f"CONTEXT FROM OTHER MODULES:\n{outros_modulos or NO_CONTEXT}"
    )


def msg_esclarecedor(
    nome: str, descricao: str, analise: ModuleAnalysis, outros_modulos: str = ""
) -> str:
    return (
        f"MODULE TO BE CLARIFIED: {nome}\n\n"
        f"ORIGINAL DESCRIPTION:\n{descricao}\n\n"
        f"MODULE ANALYSIS (JSON):\n{analise.model_dump_json(indent=2)}\n\n"
        f"CONTEXT FROM OTHER MODULES:\n{outros_modulos or NO_CONTEXT}"
    )


def msg_modelador_dados(
    nome: str, especificacao: FinalSpecification, outros_modulos: str = ""
) -> str:
    return (
        f"MODULE: {nome}\n\n"
        f"FINAL SPECIFICATION (JSON):\n{especificacao.model_dump_json(indent=2)}\n\n"
        f"CONTEXT FROM OTHER MODULES:\n{outros_modulos or NO_CONTEXT}"
    )


def msg_arquiteto_software(
    nome: str,
    especificacao: FinalSpecification,
    modelo_dados: ModuleDataModel,
    outros_modulos: str = "",
) -> str:
    return (
        f"MODULE: {nome}\n\n"
        f"FINAL SPECIFICATION (JSON):\n{especificacao.model_dump_json(indent=2)}\n\n"
        "VALIDATED DATA MODEL (JSON) — closed, do not alter:\n"
        f"{modelo_dados.model_dump_json(indent=2)}\n\n"
        f"CONTEXT FROM OTHER MODULES:\n{outros_modulos or NO_CONTEXT}"
    )


# ------------------------------------------------------ implementação
MARCADOR_LOTE = "FILES TO GENERATE IN THIS BATCH (JSON):"
NO_BASE = "(no base plan)"
NO_FILES = "(none)"


def resumo_base(plano_base: Optional[ImplementationPlan], lado: str = "") -> str:
    """Convenções e arquivos da base (caminho, tipo, exports) para os módulos."""
    if not plano_base:
        return NO_BASE
    linhas = ["Conventions:"] + [f"- {c}" for c in plano_base.conventions]
    lados = {"backend": plano_base.backend_files, "frontend": plano_base.frontend_files}
    for nome, arquivos in lados.items():
        if lado and nome != lado:
            continue
        linhas.append(f"{nome.capitalize()} files:")
        linhas += [f"- {f.path} [{f.kind}]: {f.purpose}"
                   f"{' Exports: ' + ', '.join(f.exports) if f.exports else ''}"
                   for f in arquivos]
    return "\n".join(linhas)


def _lote(arquivos: List[PlannedFile]) -> str:
    return MARCADOR_LOTE + "\n" + json.dumps(
        [f.model_dump() for f in arquivos], indent=2, ensure_ascii=False)


def _arquivos(plano_lado: List[PlannedFile]) -> str:
    return json.dumps([f.model_dump() for f in plano_lado], indent=2, ensure_ascii=False)


# ------------------------------------------------- recorte por lote (sandbox)
# A mensagem é reenviada a cada passo do agente; só 1–2% dela era específica
# do lote (relato 9). No sandbox, cada lote recebe só o que usa, e os documentos
# inteiros ficam em disco para o agente ler se precisar.
KINDS_COM_TABELA = {"entity", "repository", "mapper", "test"}
KINDS_COM_SERVICOS = {"public_api", "service", "config", "test"}
KINDS_COM_EVENTOS = {"event", "service", "test"}
KINDS_COM_DEPENDENCIAS = {"service", "public_api", "config", "test"}


def _kinds(lote: List[PlannedFile]) -> set:
    return {f.kind for f in lote}


def _reqs_citados(lote: List[PlannedFile]) -> set:
    return {r for f in lote for r in f.requirements}


def _rotas_citadas(lote: List[PlannedFile]) -> set:
    return {e.strip() for f in lote for e in f.endpoints}


def _endpoint_citado(e, citadas: set) -> bool:
    return f"{e.method} {e.path}" in citadas or e.path in citadas


def _simples(nome: str) -> str:
    return nome.split(".")[-1].split("(")[0].strip()


# Tipos de arquivo cujos exports aparecem no request/response dos endpoints
KINDS_DE_PAYLOAD = {"dto", "types"}


def _payloads(lote: List[PlannedFile]) -> set:
    """Nomes exportados pelos DTOs (backend) e tipos (frontend) do lote."""
    return {_simples(x) for f in lote if f.kind in KINDS_DE_PAYLOAD for x in f.exports
            if len(_simples(x)) >= 4}


def _usa_payload(e, nomes: set) -> bool:
    """O request ou o response do endpoint cita um dos nomes (palavra inteira)."""
    texto = f"{e.request_summary} {e.response_summary}"
    return any(re.search(rf"\b{re.escape(n)}\b", texto) for n in nomes)


def _endpoints_do_lote(arq: ModuleArchitecture, lote: List[PlannedFile]) -> list:
    """Endpoints citados pelos arquivos do lote e, para DTOs e tipos, os que os
    usam no request ou response (o agente buscava esses endpoints no documento
    da arquitetura; relato 9, lotes 7 e 8)."""
    citadas, nomes = _rotas_citadas(lote), _payloads(lote)
    return [e for e in arq.endpoints
            if _endpoint_citado(e, citadas) or (nomes and _usa_payload(e, nomes))]


def consumidores(f: PlannedFile, plano_lado: List[PlannedFile]) -> List[PlannedFile]:
    """Arquivos do plano que importam `f` (depends_on)."""
    return [c for c in plano_lado if f.path in c.depends_on and c.path != f.path]


# Arquivos cujos consumidores o agente procurava no plano (relato 9: "quem usa
# os DTOs do lote"). Em lote de entidade a lista chegava a 9 mil caracteres por
# passo sem evidência de uso, por isso fica restrita a estes tipos.
KINDS_COM_USADO_POR = {"dto", "event", "types"}


def usado_por(plano_lado: List[PlannedFile], lote: List[PlannedFile]) -> str:
    """Quem usa cada DTO, evento ou tipo do lote: os consumidores planejados,
    quase sempre gerados em lotes seguintes (o agente os procurava com grep)."""
    linhas = []
    for f in lote:
        cs = consumidores(f, plano_lado) if f.kind in KINDS_COM_USADO_POR else []
        if cs:
            linhas.append(f"- {f.path} is used by:")
            linhas += [f"  - {c.path} [{c.kind}]: {c.purpose[:160]}" for c in cs]
    return "\n".join(linhas) or NO_FILES


def recorte_especificacao(spec: FinalSpecification, lote: List[PlannedFile]) -> str:
    """Escopo, requisitos citados pelos arquivos do lote e as decisões que os afetam."""
    citados = _reqs_citados(lote)
    reqs = [r.model_dump() for r in
            list(spec.functional_requirements) + list(spec.non_functional_requirements)
            if r.id in citados]
    decisoes = [{"id": p.id, "title": p.title, "adopted_decision": p.adopted_decision}
                for p in spec.resolved_problems
                if any(c in (p.requirements_impact or "") for c in citados)]
    return json.dumps({"module_name": spec.module_name, "final_scope": spec.final_scope,
                       "requirements_of_this_batch": reqs,
                       "decisions_affecting_them": decisoes},
                      indent=2, ensure_ascii=False)


def recorte_arquitetura(arq: ModuleArchitecture, lote: List[PlannedFile]) -> str:
    """Só as partes da arquitetura que os arquivos do lote implementam ou usam."""
    kinds = _kinds(lote)
    exports = {_simples(x) for f in lote for x in f.exports}
    citados = _reqs_citados(lote)
    parte: Dict[str, object] = {"module_name": arq.module_name,
                                "java_package": arq.java_package,
                                "package_structure": arq.package_structure}
    endpoints = [e.model_dump() for e in _endpoints_do_lote(arq, lote)]
    if endpoints:
        parte["endpoints"] = endpoints
    servicos = [s.model_dump() for s in arq.public_services
                if kinds & KINDS_COM_SERVICOS or _simples(s.interface) in exports]
    if servicos:
        parte["public_services"] = servicos
    eventos = [e.model_dump() for e in arq.domain_events
               if kinds & KINDS_COM_EVENTOS or _simples(e.name) in exports]
    if eventos:
        parte["domain_events"] = eventos
    if kinds & KINDS_COM_DEPENDENCIAS and arq.module_dependencies:
        parte["module_dependencies"] = [d.model_dump() for d in arq.module_dependencies]
    decisoes = [d.model_dump() for d in arq.architectural_decisions
                if any(c in (d.requirement or "") for c in citados)]
    if decisoes:
        parte["architectural_decisions"] = decisoes
    return json.dumps(parte, indent=2, ensure_ascii=False)


def resumo_plano(plano_lado: List[PlannedFile], lote: List[PlannedFile]) -> str:
    """Os outros arquivos do módulo, uma linha cada (os do lote vêm completos no fim)."""
    no_lote = {f.path for f in lote}
    return "\n".join(f"- {f.path} [{f.kind}]"
                     f"{': ' + ', '.join(f.exports) if f.exports else ''}"
                     for f in plano_lado if f.path not in no_lote) or NO_FILES


def usa_ddl(lote: List[PlannedFile]) -> bool:
    return bool(_kinds(lote) & KINDS_COM_TABELA or any(f.tables for f in lote))


def msg_planejador(
    nome: str,
    especificacao: FinalSpecification,
    modelo_dados: ModuleDataModel,
    arquitetura: ModuleArchitecture,
    lay: Layout,
    tem_frontend: bool,
    migracao: str,
    ddl: str,
    plano_base: Optional[ImplementationPlan],
    outros_modulos: str = "",
) -> str:
    front = (f"- frontend module files: {lay.frontend_prefixo}\n"
             f"- module routes file: {lay.rotas}.<ext> (kind 'routes')\n"
             if tem_frontend else "- frontend: none (leave frontend_files and screens empty)\n")
    return (
        f"MODE: MODULE PLAN\nMODULE: {nome}\n\n"
        f"FOLDERS:\n- backend module files: {lay.backend_prefixos[0]}\n"
        f"- backend tests: {lay.backend_prefixos[1]}\n{front}\n"
        f"GENERATED MIGRATION ({migracao}) — already exists, do not plan it:\n"
        f"{ddl or '(no SQL database in the PROJECT CONTEXT)'}\n\n"
        f"FINAL SPECIFICATION (JSON):\n{especificacao.model_dump_json(indent=2)}\n\n"
        f"VALIDATED DATA MODEL (JSON):\n{modelo_dados.model_dump_json(indent=2)}\n\n"
        f"VALIDATED ARCHITECTURE (JSON):\n{arquitetura.model_dump_json(indent=2)}\n\n"
        f"BASE PLAN (shared files, do not re-plan):\n{resumo_base(plano_base)}\n\n"
        f"CONTEXT FROM OTHER MODULES:\n{outros_modulos or NO_CONTEXT}"
    )


def msg_planejador_base(modulos: List[str], rotas: List[str],
                        proibidos: List[str], tem_frontend: bool) -> str:
    front = ("Module routes files the router must import (one per module, "
             "extension chosen by the stack):\n" + "\n".join(f"- {r}" for r in rotas)
             if tem_frontend else "Frontend: none (leave frontend_files empty).")
    return (
        "MODE: BASE PLAN\n\n"
        f"MODULES: {', '.join(modulos)}\n\n"
        "Folders reserved for modules (do not plan anything inside them):\n"
        + "\n".join(f"- {p}" for p in proibidos)
        + f"\n\nShared SQL (only if needed, e.g. outbox table): prefix "
        f"{PREFIXO_MIGRACAO_BASE}\n\n{front}"
    )


def msg_implementador_backend(
    nome: str,
    especificacao: FinalSpecification,
    arquitetura: ModuleArchitecture,
    plano: ImplementationPlan,
    plano_base: Optional[ImplementationPlan],
    ddl: str,
    externas: str,
    dependencias: str,
    lote: List[PlannedFile],
    i: int,
    n: int,
    api_base: str = "",
    documentos: str = "",
) -> str:
    """Mensagem de um lote do backend. Com `documentos` (sandbox), vai só o recorte
    do lote; os documentos inteiros ficam nos caminhos indicados."""
    if documentos:
        ddl_lote = (ddl or "(no SQL database)") if usa_ddl(lote) else \
            "(not needed for this batch; full DDL in the documents above)"
        return (
            f"MODE: MODULE\nMODULE: {nome} — batch {i}/{n}\n\n"
            f"{documentos}\n\n"
            f"SPECIFICATION — scope and requirements of this batch (JSON):\n"
            f"{recorte_especificacao(especificacao, lote)}\n\n"
            f"ARCHITECTURE — parts used by this batch (JSON):\n"
            f"{recorte_arquitetura(arquitetura, lote)}\n\n"
            f"MIGRATION (DDL, closed):\n{ddl_lote}\n\n"
            f"OTHER FILES OF THE MODULE (path [kind]: exports):\n"
            f"{resumo_plano(plano.backend_files, lote)}\n"
            f"Implementation notes:\n" + "\n".join(f"- {x}" for x in plano.implementation_notes)
            + f"\n\nWHO USES THE FILES OF THIS BATCH (planned consumers, usually "
            f"generated in later batches):\n{usado_por(plano.backend_files, lote)}\n\n"
            f"BASE:\n{resumo_base(plano_base, 'backend')}\n\n"
            f"OTHER MODULES' PUBLIC CODE:\n{externas or NO_FILES}\n\n"
            f"FILES THIS BATCH DEPENDS ON (public signatures; base files included):\n"
            f"{dependencias or NO_FILES}\n\n"
            + _lote(lote)
        )
    return (
        f"MODE: MODULE\nMODULE: {nome} — batch {i}/{n}\n\n"
        f"FINAL SPECIFICATION (JSON):\n{especificacao.model_dump_json(indent=2)}\n\n"
        f"ARCHITECTURE (JSON):\n{arquitetura.model_dump_json(indent=2)}\n\n"
        f"MIGRATION (DDL, closed):\n{ddl or '(no SQL database)'}\n\n"
        f"IMPLEMENTATION PLAN — backend files (JSON):\n{_arquivos(plano.backend_files)}\n"
        f"Implementation notes:\n" + "\n".join(f"- {x}" for x in plano.implementation_notes)
        + f"\n\nBASE:\n{resumo_base(plano_base, 'backend')}\n\n"
        f"BASE API (public signatures; use these exact constructors and methods):\n"
        f"{api_base or NO_FILES}\n\n"
        f"OTHER MODULES' PUBLIC CODE:\n{externas or NO_FILES}\n\n"
        f"FILES THIS BATCH DEPENDS ON (full content or public signatures):\n"
        f"{dependencias or NO_FILES}\n\n"
        + _lote(lote)
    )


def msg_implementador_frontend(
    nome: str,
    especificacao: FinalSpecification,
    arquitetura: ModuleArchitecture,
    plano: ImplementationPlan,
    plano_base: Optional[ImplementationPlan],
    dependencias: str,
    lote: List[PlannedFile],
    i: int,
    n: int,
    api_base: str = "",
    documentos: str = "",
) -> str:
    """Mensagem de um lote do frontend. Com `documentos` (sandbox), só o recorte."""
    if documentos:
        citados = _reqs_citados(lote)
        no_lote = {f.path for f in lote}
        reqs = [r.model_dump() for r in
                list(especificacao.functional_requirements)
                + list(especificacao.non_functional_requirements) if r.id in citados]
        endpoints = [e.model_dump() for e in _endpoints_do_lote(arquitetura, lote)]
        telas = [s.model_dump() for s in plano.screens
                 if s.page_file in no_lote or "routes" in _kinds(lote)]
        return (
            f"MODE: MODULE\nMODULE: {nome} — batch {i}/{n}\n\n"
            f"{documentos}\n\n"
            f"REQUIREMENTS OF THIS BATCH (JSON):\n{json.dumps(reqs, indent=2, ensure_ascii=False)}\n\n"
            f"API ENDPOINTS USED BY THIS BATCH (JSON):\n"
            f"{json.dumps(endpoints, indent=2, ensure_ascii=False)}\n\n"
            f"SCREENS OF THIS BATCH (JSON):\n{json.dumps(telas, indent=2, ensure_ascii=False)}\n\n"
            f"OTHER FILES OF THE MODULE (path [kind]: exports):\n"
            f"{resumo_plano(plano.frontend_files, lote)}\n\n"
            f"WHO USES THE FILES OF THIS BATCH (planned consumers, usually "
            f"generated in later batches):\n{usado_por(plano.frontend_files, lote)}\n\n"
            f"BASE:\n{resumo_base(plano_base, 'frontend')}\n\n"
            f"FILES THIS BATCH DEPENDS ON (public signatures; base files included):\n"
            f"{dependencias or NO_FILES}\n\n"
            + _lote(lote)
        )
    reqs = [r.model_dump() for r in
            list(especificacao.functional_requirements)
            + list(especificacao.non_functional_requirements)]
    return (
        f"MODE: MODULE\nMODULE: {nome} — batch {i}/{n}\n\n"
        f"REQUIREMENTS (JSON):\n{json.dumps(reqs, indent=2, ensure_ascii=False)}\n\n"
        "API ENDPOINTS (JSON):\n"
        + json.dumps([e.model_dump() for e in arquitetura.endpoints], indent=2,
                     ensure_ascii=False)
        + "\n\nSCREENS (JSON):\n"
        + json.dumps([t.model_dump() for t in plano.screens], indent=2, ensure_ascii=False)
        + f"\n\nIMPLEMENTATION PLAN — frontend files (JSON):\n{_arquivos(plano.frontend_files)}"
        f"\n\nBASE:\n{resumo_base(plano_base, 'frontend')}\n\n"
        f"BASE API (public signatures; use these exact props, types and field names):\n"
        f"{api_base or NO_FILES}\n\n"
        f"FILES THIS BATCH DEPENDS ON (full content or public signatures):\n"
        f"{dependencias or NO_FILES}\n\n"
        + _lote(lote)
    )


def msg_implementador_base(
    lado: str,
    modulos: List[str],
    rotas: List[str],
    plano_base: ImplementationPlan,
    dependencias: str,
    lote: List[PlannedFile],
    i: int,
    n: int,
    endpoints: Optional[list] = None,
) -> str:
    arquivos = plano_base.backend_files if lado == "backend" else plano_base.frontend_files
    api = ("\n\nKNOWN API ENDPOINTS of the modules (use these exact paths, e.g. for "
           "login and token refresh; do not invent others):\n"
           + json.dumps([{"method": e.method, "path": e.path, "purpose": e.purpose,
                          "request": e.request_summary, "response": e.response_summary}
                         for e in endpoints], indent=2, ensure_ascii=False)
           if endpoints else "")
    extra = ("\nModule routes files the router imports (they will exist; import "
             "them without extension):\n" + "\n".join(f"- {r}" for r in rotas)
             if lado == "frontend" else "")
    return (
        f"MODE: BASE — batch {i}/{n}\n\nMODULES: {', '.join(modulos)}{extra}{api}\n\n"
        "Conventions:\n" + "\n".join(f"- {c}" for c in plano_base.conventions)
        + f"\n\nBASE PLAN — {lado} files (JSON):\n{_arquivos(arquivos)}\n\n"
        f"FILES THIS BATCH DEPENDS ON (full content or public signatures):\n"
        f"{dependencias or NO_FILES}\n\n"
        + _lote(lote)
    )


def verificacao_do_lote(lote: List[PlannedFile], raiz_container: str, lado: str) -> str:
    """Comando de verificação proporcional ao lote, com saída curta (relato 9)."""
    kinds = _kinds(lote)
    if lado == "frontend":
        cmd = f"cd {raiz_container} && npx tsc --noEmit 2>&1 | tail -n 40"
        testar = bool(kinds & {"page", "routes"})
    else:
        testes = [f.path.rsplit("/", 1)[-1].rsplit(".", 1)[0] for f in lote
                  if f.kind == "test" or "/test/" in f"/{f.path}"]
        if testes:
            cmd = (f"cd {raiz_container} && mvn -q test -Dtest={','.join(testes)} "
                   f"-Dsurefire.failIfNoSpecifiedTests=false 2>&1 | tail -n 60")
        else:
            cmd = f"cd {raiz_container} && mvn -q compile 2>&1 | tail -n 40"
        testar = "controller" in kinds
    teste = ("FEATURE TEST: yes (this batch has controllers/pages: you may start the "
             "application and exercise them)" if testar else
             "FEATURE TEST: no (do not start the application)")
    return f"VERIFY: {cmd}\n{teste}"


def documento_jsonl(obj: BaseModel) -> str:
    """Documento em JSON Lines: um item por linha, com a seção de origem.

    Antes ia como JSON indentado (o plano do Customers tinha 1.726 linhas):
    read_file lê 100 linhas por vez e um grep devolvia só a linha que casou
    (`"path": ...`), sem o resto do item, e o agente lia de novo para completar
    (relato 9). Com um item por linha, grep com output_mode "content" devolve o
    item inteiro.
    """
    linhas = []
    for campo, valor in obj.model_dump().items():
        if isinstance(valor, list) and valor:
            linhas += [{"section": campo, **item} if isinstance(item, dict)
                       else {"section": campo, "value": item} for item in valor]
        else:
            linhas.append({"section": campo, "value": valor})
    return "\n".join(json.dumps(l, ensure_ascii=False) for l in linhas) + "\n"


def documentos_do_modulo(caminhos: Dict[str, str]) -> str:
    """Bloco com os documentos inteiros gravados no sandbox (lidos só se preciso)."""
    if not caminhos:
        return ""
    return ("FULL DOCUMENTS (only if this message is not enough; the .jsonl files have "
            "one entry per line: grep a path, class name or requirement ID with "
            "output_mode \"content\" in the one document that has it; never read_file "
            "them whole):\n"
            + "\n".join(f"- {c} — {d}" for d, c in caminhos.items()))


def msg_sandbox(lote: List[PlannedFile], raiz_container: str, ambiente: str,
                lado: str = "") -> str:
    """Complemento da mensagem de um lote no modo sandbox."""
    caminhos = "\n".join(f"- {raiz_container}/{f.path}" for f in lote)
    lado = lado or ("frontend" if raiz_container.rstrip("/").endswith("frontend") else "backend")
    return (
        f"\n\nSANDBOX:\nSide root: {raiz_container}\n"
        f"Write exactly these files (absolute paths):\n{caminhos}\n\n"
        f"{verificacao_do_lote(lote, raiz_container, lado)}\n\n"
        f"Services in the Docker network:\n{ambiente or '- (none)'}"
    )
