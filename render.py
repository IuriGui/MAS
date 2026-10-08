"""Converte os artefatos estruturados em markdown legível para humanos."""
from typing import List

from schemas import (FinalSpecification, ImplementationPlan, ModuleAnalysis,
                     ModuleArchitecture, ModuleDataModel, ProjectContext)


def _lista(itens: List[str]) -> str:
    return "\n".join(f"- {i}" for i in itens) if itens else "_Nenhum._"


def _reqs(reqs) -> str:
    return _lista([f"**{r.id}** — {r.description}" for r in reqs])


def _deps(deps) -> str:
    return _lista([f"**{d.module}** ({d.nature}): {d.reason}" for d in deps])


def render_contexto(nome: str, c: ProjectContext) -> str:
    stack = "\n".join(
        f"| {campo} | {t.name} | {t.version} | {t.justification} |"
        for campo, t in c.tech_stack)
    cc = c.cross_cutting_decisions
    transversais = _lista([
        f"**Multi-tenancy:** {'sim' if cc.multi_tenancy.enabled else 'não'} — "
        f"{cc.multi_tenancy.strategy}. {cc.multi_tenancy.details}",
        f"**Autenticação:** {cc.authentication_and_authorization.token_type}; RBAC: "
        f"{cc.authentication_and_authorization.rbac_enforcement}",
        f"**Erros:** {cc.error_format_and_status_codes.format}",
        f"**Logs/auditoria:** {cc.logging_audit_and_observability.logging} "
        f"{cc.logging_audit_and_observability.audit}",
        f"**Configuração:** {cc.configuration_and_secrets.strategy}",
        f"**Dados:** ID {cc.data_conventions.id_type}; "
        f"{cc.data_conventions.timestamps_and_timezone}; "
        f"{cc.data_conventions.soft_delete}; {cc.data_conventions.money_and_decimals}",
        f"**API:** {cc.api_conventions.style}; {cc.api_conventions.versioning}; "
        f"{cc.api_conventions.pagination}",
    ])
    nc = c.naming_conventions
    mods = "\n".join(
        f"| {m.name} | {m.kind} | {m.responsibility} | {', '.join(m.depends_on) or '—'} |"
        for m in c.module_map)
    adrs = "\n\n".join(
        f"### {d.id} — {d.decision}\n- Contexto: {d.context}\n"
        f"- Opções: {'; '.join(d.options_considered)}\n- Consequências: {d.consequences}"
        for d in c.decisions) or "_Nenhuma._"
    return (
        f"# Contexto do projeto: {nome}\n\nEstilo: **{c.architectural_style}**\n\n"
        "## Stack\n\n| Item | Tecnologia | Versão | Justificativa |\n|---|---|---|---|\n"
        f"{stack}\n\n## Decisões transversais\n{transversais}\n\n"
        f"## Convenções de nomes\n- Tabelas: {nc.tables}\n- Colunas: {nc.columns}\n"
        f"- Endpoints: {nc.endpoints}\n- Eventos: {nc.events}\n- Pacotes: {nc.packages}\n\n"
        "## Mapa de módulos\n\n| Módulo | Tipo | Responsabilidade | Depende de |\n"
        f"|---|---|---|---|\n{mods}\n\n"
        f"**Ordem de construção:** {' → '.join(c.build_order)}\n\n"
        f"## Decisões (ADR)\n\n{adrs}\n\n## Riscos\n"
        + _lista([f"[{r.severity}] {r.risk} — {r.mitigation}" for r in c.risks])
        + "\n\n## Premissas\n"
        + _lista([f"**{a.id}** {a.decision} — {a.reason} (impacto se errada: "
                  f"{a.impact_if_wrong})" for a in c.assumptions])
        + "\n"
    )


def render_analise(a: ModuleAnalysis) -> str:
    probs = "\n\n".join(
        f"### {p.id} — {p.title}\n- Tipo: {p.type}\n- Severidade: {p.severity}\n"
        f"- Problema: {p.description}\n- Sugestão: {p.correction_suggestion}"
        for p in a.problems) or "_Nenhum problema encontrado._"
    return (
        f"## Análise do módulo\n\n{a.scope_summary}\n\n### Problemas\n\n{probs}\n\n"
        f"### Requisitos funcionais\n{_reqs(a.functional_requirements)}\n\n"
        f"### Requisitos não-funcionais\n{_reqs(a.non_functional_requirements)}\n\n"
        f"### Dependências\n{_deps(a.dependencies)}\n\n"
        f"### Observações\n{_lista(a.observations)}"
    )


def render_especificacao(e: FinalSpecification) -> str:
    probs = "\n\n".join(
        f"### {p.id} — {p.title}\n- Tipo/Severidade: {p.type} / {p.severity}\n"
        f"- Problema: {p.problem}\n- Decisão: {p.adopted_decision}\n"
        f"- Justificativa: {p.justification}\n- Confiança: **{p.confidence}**\n"
        f"- Impacto: {p.requirements_impact}"
        for p in e.resolved_problems) or "_Nenhum._"
    return (
        f"## Especificação final\n\n### Escopo\n{e.final_scope}\n\n"
        f"### Alertas para revisão humana\n{_lista(e.review_alerts)}\n\n"
        f"### Problemas resolvidos\n\n{probs}\n\n"
        f"### Requisitos funcionais\n{_reqs(e.functional_requirements)}\n\n"
        f"### Requisitos não-funcionais\n{_reqs(e.non_functional_requirements)}\n\n"
        f"### Interdependências\n{_deps(e.interdependencies)}\n\n"
        f"### Especificação consolidada\n{e.consolidated_specification}"
    )


def _ref(valor):
    return None if not valor or valor.strip().lower() in ("null", "none") else valor


def render_modelo_dados(m: ModuleDataModel) -> str:
    ents = []
    for e in m.entities:
        linhas = "\n".join(
            f"| {c.name} | {c.sql_type} | {'sim' if c.required else 'não'} | "
            f"{'PK' if c.primary_key else (('FK → ' + _ref(c.reference)) if _ref(c.reference) else '')}"
            f"{' ' + '; '.join(c.constraints) if c.constraints else ''} |"
            for c in e.columns)
        idx = _lista([
            f"`{ix.name}` ({', '.join(ix.columns)})"
            f"{' UNIQUE' if ix.unique else ''}"
            f"{(' WHERE ' + ix.where) if _ref(ix.where) else ''} — {ix.requirement}"
            for ix in e.indexes])
        ents.append(
            f"#### `{e.table}`\n{e.purpose}\n\n| Campo | Tipo | Obrig. | Chave/Restr. |\n"
            f"|---|---|---|---|\n{linhas}\n\n**Índices**\n{idx}\n\n"
            f"Restrições: {', '.join(e.constraints) or '—'}")
    return (
        "## Modelo de dados\n\n### Entidades\n\n" + "\n\n".join(ents)
        + "\n\n### Relacionamentos\n"
        + _lista([f"{r.source} {r.cardinality} {r.target}"
                  f"{(' (via ' + r.via + ')') if _ref(r.via) else ''} — {r.description}"
                  for r in m.relationships])
        + "\n\n### Referências a outros módulos (IDs lógicos)\n"
        + _lista([f"`{x.table}.{x.column}` → **{x.module}**: {x.reason}"
                  for x in m.external_references])
        + f"\n\n### Regras de integridade\n{_lista(m.integrity_rules)}"
        + f"\n\n### Premissas\n{_lista(m.assumptions)}"
        + f"\n\n### Resumo\n{m.data_model_summary}"
    )


def render_arquitetura(a: ModuleArchitecture) -> str:
    eps = "\n".join(
        f"| {x.method} | `{x.path}` | {x.purpose} | "
        f"{', '.join(x.roles) if x.requires_authentication else 'público'} | "
        f"{', '.join(map(str, x.http_status_codes))} | "
        f"{', '.join(x.tables_touched) or '—'} | "
        f"{', '.join(x.addressed_requirements)} |" for x in a.endpoints)
    return (
        f"## Arquitetura do módulo\n\n`{a.java_package}`\n\n"
        + "### Estrutura de pacotes\n```\n" + "\n".join(a.package_structure) + "\n```"
        + "\n\n### Endpoints\n\n| Método | Caminho | Finalidade | Acesso | Status | Tabelas | Req. |\n"
        "|---|---|---|---|---|---|---|\n" + eps
        + "\n\n### Serviços públicos\n"
        + _lista([f"`{s.interface}.{s.method}({s.parameters})` → {s.return_type}: {s.purpose}"
                  f"{(' (consumidor: ' + s.consumer_module + ')') if _ref(s.consumer_module) else ''}"
                  for s in a.public_services])
        + "\n\n### Eventos de domínio\n"
        + _lista([f"**{e.name}** — {e.trigger}. Payload: {e.payload}. "
                  f"Consumidores: {', '.join(e.consumers) or '—'}"
                  f"{' (outbox)' if e.uses_outbox else ''}"
                  for e in a.domain_events])
        + "\n\n### Dependências de módulo\n"
        + _lista([f"{d.module}: {d.public_interface}.{d.operation} [{d.mechanism}] "
                  f"({d.direction}) — {d.reason}" for d in a.module_dependencies])
        + "\n\n### Decisões arquiteturais\n"
        + _lista([f"**[{d.topic}] {d.decision}** — {d.reason} ({d.requirement})"
                  for d in a.architectural_decisions])
        + f"\n\n### Resumo\n{a.architecture_summary}"
    )


def render_plano(p: ImplementationPlan) -> str:
    def tabela(arquivos):
        if not arquivos:
            return "_Nenhum._"
        return ("| Arquivo | Tipo | Exports | Tabelas/Endpoints | Req. |\n|---|---|---|---|---|\n"
                + "\n".join(
                    f"| `{f.path}` | {f.kind} | {', '.join(f.exports) or '—'} | "
                    f"{', '.join(f.tables + f.endpoints) or '—'} | "
                    f"{', '.join(f.requirements) or '—'} |" for f in arquivos))
    telas = _lista([f"**{t.name}** `{t.route}` ({', '.join(t.roles) or 'público'}) — "
                    f"{t.purpose}. Endpoints: {', '.join(t.endpoints) or '—'}. "
                    f"Página: `{t.page_file}`" for t in p.screens])
    return (
        "## Plano de implementação\n\n"
        f"### Backend\n\n{tabela(p.backend_files)}\n\n"
        f"### Frontend\n\n{tabela(p.frontend_files)}\n\n"
        f"### Telas\n{telas}\n\n"
        + (f"### Convenções\n{_lista(p.conventions)}\n\n" if p.conventions else "")
        + f"### Notas\n{_lista(p.implementation_notes)}"
    )


def render_arquivos(titulo: str, manifesto: List[dict]) -> str:
    arquivos = [m["path"] for m in manifesto if "path" in m]
    notas = [n for m in manifesto for n in m.get("notas", [])]
    return (f"## {titulo}\n\n{len(arquivos)} arquivo(s):\n"
            + _lista([f"`{a}`" for a in arquivos])
            + (f"\n\n### Notas do implementador\n{_lista(notas)}" if notas else ""))


def renderizar_relatorio(nome: str, artefatos: dict) -> str:
    partes = [f"# Módulo: {nome}"]
    if "analise" in artefatos:
        partes.append(render_analise(artefatos["analise"]))
    if "especificacao" in artefatos:
        partes.append(render_especificacao(artefatos["especificacao"]))
    if "modelo_dados" in artefatos:
        partes.append(render_modelo_dados(artefatos["modelo_dados"]))
    if "arquitetura" in artefatos:
        partes.append(render_arquitetura(artefatos["arquitetura"]))
    if "plano" in artefatos:
        partes.append(render_plano(artefatos["plano"]))
    if "arquivos_backend" in artefatos:
        partes.append(render_arquivos("Código backend", artefatos["arquivos_backend"]))
    if "arquivos_frontend" in artefatos:
        partes.append(render_arquivos("Código frontend", artefatos["arquivos_frontend"]))
    return "\n\n---\n\n".join(partes) + "\n"
