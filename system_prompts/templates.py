"""Builds the USER MESSAGE for each agent.

The system prompt remains static (role + rules); the module's variable data
is provided here. Previously, the {{...}} placeholders were inside the system
prompt and were never replaced.
"""
import json
from typing import Dict, List, Optional, Tuple

from layout import PREFIXO_MIGRACAO_BASE, Layout
from schemas import (FinalSpecification, ImplementationPlan, ModuleAnalysis,
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
) -> str:
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
        f"CONTENT OF FILES THIS BATCH DEPENDS ON:\n{dependencias or NO_FILES}\n\n"
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
) -> str:
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
        f"CONTENT OF FILES THIS BATCH DEPENDS ON:\n{dependencias or NO_FILES}\n\n"
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
        f"CONTENT OF FILES THIS BATCH DEPENDS ON:\n{dependencias or NO_FILES}\n\n"
        + _lote(lote)
    )


# ------------------------------------------------------ comunicação (F3)
def msg_consulta(nome: str, remetente: str, artefato, pergunta: str) -> str:
    """Pergunta de um agente (`remetente`, papel em inglês) ao dono do artefato."""
    return (
        f"MODE: CONSULTATION\nMODULE: {nome}\n\n"
        f"YOUR ARTIFACT (JSON):\n{artefato.model_dump_json(indent=2)}\n\n"
        f"QUESTION FROM THE {remetente.upper()}:\n{pergunta}"
    )


def msg_revisao(remetente: str, problemas: List[str], anterior) -> str:
    """Anexo à mensagem normal da etapa quando outro agente reportou um bloqueio."""
    return (
        f"\n\nREVISION REQUEST from the {remetente}: your previous output blocks its "
        "work. Problems reported:\n- " + "\n- ".join(problemas)
        + f"\n\nYOUR PREVIOUS OUTPUT (JSON):\n{anterior.model_dump_json(indent=2)}\n\n"
        "Produce a REVISED version that resolves these problems and keeps everything "
        "else unchanged (keep existing IDs; new items get the next free ID). If a "
        "reported problem is not really a defect of your output, keep it as it is and "
        "explain why in your notes or assumptions."
    )
