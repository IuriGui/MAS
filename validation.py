"""Validações determinísticas das saídas dos agentes.

Cada função devolve uma lista de erros (vazia = ok). Os erros são reenviados
ao agente numa nova tentativa, então devem ser claros e acionáveis.
"""
import posixpath
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import layout
from schemas import (FinalSpecification, GeneratedFiles, ImplementationPlan,
                     ModuleAnalysis, ModuleArchitecture, ModuleDataModel,
                     PlannedFile, ProjectContext)

_SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
_ID_FR = re.compile(r"^FR\d+$")
_ID_NFR = re.compile(r"^NFR\d+$")
_ID_PROB = re.compile(r"^PROB\d+$")
_ID_REQ_EM_TEXTO = re.compile(r"\b(?:N?FR)\d+\b")
_KEBAB_SEG = re.compile(r"^([a-z0-9]+(-[a-z0-9]+)*|\{[a-zA-Z][a-zA-Z0-9]*\})$")
_ID_ADR = re.compile(r"^ADR\d+$")
_ID_AS = re.compile(r"^AS\d+$")
_PREFIXO_API = re.compile(r"/api/v\d+")
_RESTRICAO_COLUNA = re.compile(
    r"^(CONSTRAINT\s+\w+\s+)?(UNIQUE|DEFAULT|CHECK|NOT NULL|NULL|PRIMARY KEY|"
    r"REFERENCES|GENERATED|COLLATE)\b", re.I)
_RESTRICAO_TABELA = re.compile(
    r"^(CONSTRAINT\s+\w+\s+)?(UNIQUE|CHECK|PRIMARY KEY|FOREIGN KEY|EXCLUDE)\b", re.I)

MAX_ADRS = 5
MAX_RISCOS = 4


@dataclass(frozen=True)
class Convencoes:
    """Regras de projeto checadas em código, derivadas do PROJECT CONTEXT."""
    pacote_base: str = "com.distributor.erp."
    colunas_base: Tuple[str, ...] = ("tenant_id", "created_at", "updated_at")
    tipo_pk: Optional[str] = "UUID"   # None = não checa o tipo da PK
    prefixo_api: str = "/api/v1/"


def convencoes(ctx: ProjectContext) -> Convencoes:
    """Extrai do contexto do projeto o que os validadores precisam."""
    pacote = ctx.naming_conventions.packages.strip()
    pacote = pacote.split("<", 1)[0] if "<" in pacote else pacote.rstrip(".") + "."
    colunas = ("created_at", "updated_at")
    if ctx.cross_cutting_decisions.multi_tenancy.enabled:
        colunas = ("tenant_id",) + colunas
    id_tipo = ctx.cross_cutting_decisions.data_conventions.id_type.upper()
    tipo_pk = "UUID" if "UUID" in id_tipo else None
    m = _PREFIXO_API.search(ctx.cross_cutting_decisions.api_conventions.versioning)
    prefixo = (m.group(0) if m else "/api/v1") + "/"
    return Convencoes(pacote_base=pacote, colunas_base=colunas,
                      tipo_pk=tipo_pk, prefixo_api=prefixo)


PADRAO = Convencoes()


def _duplicados(ids: List[str]) -> List[str]:
    return sorted({i for i in ids if ids.count(i) > 1})


def _ids_requisitos(spec: FinalSpecification) -> Set[str]:
    return {r.id for r in spec.functional_requirements} | {
        r.id for r in spec.non_functional_requirements}


def _ref(valor: Optional[str]) -> Optional[str]:
    """Modelos às vezes devolvem a string 'null' em vez de null."""
    if valor is None or valor.strip().lower() in ("", "null", "none"):
        return None
    return valor.strip()


def _validar_requisitos(rfs, rnfs) -> List[str]:
    erros = []
    for r in rfs:
        if not _ID_FR.match(r.id):
            erros.append(f"ID de requisito funcional inválido: '{r.id}' (use FRxx).")
    for r in rnfs:
        if not _ID_NFR.match(r.id):
            erros.append(f"ID de requisito não-funcional inválido: '{r.id}' (use NFRxx).")
    dup = _duplicados([r.id for r in list(rfs) + list(rnfs)])
    if dup:
        erros.append(f"IDs de requisito duplicados: {dup}.")
    return erros


# --------------------------------------------------------- 0. Arquiteto-chefe
def _chave(nome: str) -> str:
    return nome.strip().lower()


def _ciclo(grafo: Dict[str, List[str]]) -> Optional[List[str]]:
    """Devolve um ciclo (lista de módulos) se existir, senão None."""
    estado: Dict[str, int] = {}  # 1 = visitando, 2 = concluído
    pilha: List[str] = []

    def visitar(n: str) -> Optional[List[str]]:
        estado[n] = 1
        pilha.append(n)
        for d in grafo.get(n, []):
            if estado.get(d) == 1:
                return pilha[pilha.index(d):] + [d]
            if d not in estado:
                achou = visitar(d)
                if achou:
                    return achou
        pilha.pop()
        estado[n] = 2
        return None

    for n in grafo:
        if n not in estado:
            achou = visitar(n)
            if achou:
                return achou
    return None


def validar_contexto(c: ProjectContext, modulos_declarados: List[str]) -> List[str]:
    erros = []
    nomes = {_chave(m.name): m.name for m in c.module_map}

    dup = _duplicados([_chave(m.name) for m in c.module_map])
    if dup:
        erros.append(f"Módulos duplicados em module_map: {dup}.")

    faltando = [m for m in modulos_declarados if _chave(m) not in nomes]
    if faltando:
        erros.append(f"Módulos declarados ausentes do module_map: {faltando}. "
                     "Use os nomes exatamente como declarados.")
    declarados = {_chave(m) for m in modulos_declarados}
    inventados = [m.name for m in c.module_map
                  if _chave(m.name) not in declarados and m.kind != "platform"]
    if inventados:
        erros.append(f"Módulos não declarados em module_map: {inventados}. "
                     "Só é permitido acrescentar módulos kind 'platform'.")

    grafo: Dict[str, List[str]] = {}
    for m in c.module_map:
        deps = []
        for d in m.depends_on:
            if _chave(d) == _chave(m.name):
                erros.append(f"Módulo '{m.name}' depende de si mesmo.")
            elif _chave(d) not in nomes:
                erros.append(f"Módulo '{m.name}' depende de '{d}', que não está no module_map.")
            else:
                deps.append(_chave(d))
        grafo[_chave(m.name)] = deps
    ciclo = _ciclo(grafo)
    if ciclo:
        erros.append("Dependência circular entre módulos: "
                     + " -> ".join(nomes[n] for n in ciclo)
                     + ". Resolva (ex.: inverta uma das dependências usando eventos).")

    ordem = [_chave(n) for n in c.build_order]
    dup = _duplicados(ordem)
    if dup:
        erros.append(f"build_order repete módulos: {dup}.")
    fora = sorted(set(nomes) - set(ordem))
    if fora:
        erros.append(f"build_order não inclui: {[nomes[n] for n in fora]}.")
    estranhos = [n for n in c.build_order if _chave(n) not in nomes]
    if estranhos:
        erros.append(f"build_order cita módulos fora do module_map: {estranhos}.")
    if not ciclo:
        pos = {n: i for i, n in enumerate(ordem)}
        for m, deps in grafo.items():
            for d in deps:
                if m in pos and d in pos and pos[d] > pos[m]:
                    erros.append(f"build_order coloca '{nomes[m]}' antes de sua "
                                 f"dependência '{nomes[d]}'.")

    if len(c.decisions) > MAX_ADRS:
        erros.append(f"decisions tem {len(c.decisions)} ADRs; máximo {MAX_ADRS}.")
    if len(c.risks) > MAX_RISCOS:
        erros.append(f"risks tem {len(c.risks)} itens; máximo {MAX_RISCOS}.")
    baixos = [r.risk[:60] for r in c.risks if r.severity == "low"]
    if baixos:
        erros.append(f"risks deve ter só severidade high/medium: {baixos}.")
    for d in c.decisions:
        if not _ID_ADR.match(d.id):
            erros.append(f"ID de ADR inválido: '{d.id}' (use ADRxx).")
    for a in c.assumptions:
        if not _ID_AS.match(a.id):
            erros.append(f"ID de premissa inválido: '{a.id}' (use ASxx).")
    return erros


# --------------------------------------------------------- 1. Requisitos
def validar_analise(a: ModuleAnalysis) -> List[str]:
    erros = []
    if not a.functional_requirements:
        erros.append("Nenhum requisito funcional foi derivado.")
    for p in a.problems:
        if not _ID_PROB.match(p.id):
            erros.append(f"ID de problema inválido: '{p.id}' (use PROBxx).")
    dup = _duplicados([p.id for p in a.problems])
    if dup:
        erros.append(f"IDs de problema duplicados: {dup}.")
    erros += _validar_requisitos(a.functional_requirements, a.non_functional_requirements)
    return erros


# --------------------------------------------------------- 2. Esclarecedor
def validar_especificacao(e: FinalSpecification, analise: ModuleAnalysis) -> List[str]:
    erros = []

    esperados = {p.id for p in analise.problems}
    resolvidos = [p.id for p in e.resolved_problems]
    faltando = sorted(esperados - set(resolvidos))
    inventados = sorted(set(resolvidos) - esperados)
    if faltando:
        erros.append(f"Problemas sem resolução: {faltando}. Resolva TODOS.")
    if inventados:
        erros.append(f"Problemas resolvidos que não existem na análise: {inventados}.")
    dup = _duplicados(resolvidos)
    if dup:
        erros.append(f"Problemas resolvidos em duplicidade: {dup}.")

    baixas = [p for p in e.resolved_problems if p.confidence == "low"]
    if baixas and not e.review_alerts:
        erros.append(
            "Há problemas com confiança 'low' "
            f"({[p.id for p in baixas]}) mas review_alerts está vazio."
        )

    if not e.consolidated_specification.strip():
        erros.append("consolidated_specification está vazia.")

    erros += _validar_requisitos(e.functional_requirements, e.non_functional_requirements)
    return erros


# --------------------------------------------------------- 3. Modelador de dados
def validar_modelo_dados(m: ModuleDataModel, spec: FinalSpecification,
                         conv: Convencoes = PADRAO) -> List[str]:
    erros = []
    ids_req = _ids_requisitos(spec)

    if not m.entities:
        erros.append("O modelo de dados não tem nenhuma entidade.")

    tabelas = {e.table: e for e in m.entities}
    dup_tab = _duplicados([e.table for e in m.entities])
    if dup_tab:
        erros.append(f"Tabelas duplicadas: {dup_tab}.")

    for e in m.entities:
        cols = {c.name for c in e.columns}
        if not _SNAKE.match(e.table):
            erros.append(f"Tabela '{e.table}' deve estar em snake_case.")

        pks = [c for c in e.columns if c.primary_key]
        if len(pks) != 1:
            erros.append(f"Tabela '{e.table}' deve ter exatamente 1 chave primária.")
        elif pks[0].name != "id" or (
                conv.tipo_pk and pks[0].sql_type.strip().upper() != conv.tipo_pk):
            esperado = f"id {conv.tipo_pk}" if conv.tipo_pk else "id"
            erros.append(
                f"Tabela '{e.table}': a PK deve ser '{esperado}' (ver PROJECT CONTEXT).")

        faltam = [b for b in conv.colunas_base if b not in cols]
        if faltam:
            erros.append(f"Tabela '{e.table}' sem colunas obrigatórias: {faltam}.")

        dup = _duplicados([c.name for c in e.columns])
        if dup:
            erros.append(f"Tabela '{e.table}' tem colunas duplicadas: {dup}.")

        for r in e.constraints:
            if r.strip() and not _RESTRICAO_TABELA.match(r.strip()):
                erros.append(
                    f"Tabela '{e.table}': restrição '{r[:60]}' não é SQL. Escreva como "
                    "UNIQUE (...), CHECK (...) ou CONSTRAINT nome ...")
        for c in e.columns:
            if not _SNAKE.match(c.name):
                erros.append(f"Coluna '{e.table}.{c.name}' deve estar em snake_case.")
            for r in c.constraints:
                if r.strip() and not _RESTRICAO_COLUNA.match(r.strip()):
                    erros.append(
                        f"Coluna '{e.table}.{c.name}': restrição '{r[:60]}' não é SQL "
                        "(use UNIQUE, DEFAULT ..., CHECK (...)).")
            ref = _ref(c.reference)
            if not ref:
                continue
            if "." not in ref:
                erros.append(
                    f"'{e.table}.{c.name}': reference '{ref}' deve estar no "
                    "formato tabela.coluna.")
                continue
            tab_ref, col_ref = ref.split(".", 1)
            if tab_ref not in tabelas:
                # FK para outro módulo é proibida: deve ser ID lógico.
                erros.append(
                    f"'{e.table}.{c.name}' referencia '{ref}', que não pertence a "
                    "este módulo. Use ID lógico (reference = null) e declare em "
                    "external_references.")
            elif col_ref not in {x.name for x in tabelas[tab_ref].columns}:
                erros.append(
                    f"'{e.table}.{c.name}' referencia a coluna inexistente '{ref}'.")

        for ix in e.indexes:
            inexistentes = [c for c in ix.columns if c not in cols]
            if not ix.columns or inexistentes:
                erros.append(
                    f"Índice '{ix.name}' em '{e.table}' usa colunas inexistentes "
                    f"ou está vazio: {inexistentes}.")
            if ix.requirement not in ids_req:
                erros.append(
                    f"Índice '{ix.name}' em '{e.table}' justificado por requisito "
                    f"inexistente: '{ix.requirement}'.")

    for r in m.relationships:
        for lado in (r.source, r.target):
            if lado not in tabelas:
                erros.append(
                    f"Relacionamento {r.source} {r.cardinality} {r.target} cita a "
                    f"tabela '{lado}', que não está em entities.")
        via = _ref(r.via)
        if r.cardinality == "N:N" and (not via or via not in tabelas):
            erros.append(
                f"Relacionamento N:N {r.source}-{r.target} precisa de 'via' com a "
                "tabela associativa declarada em entities.")

    for x in m.external_references:
        if x.table not in tabelas:
            erros.append(
                f"external_references cita a tabela inexistente '{x.table}'.")
        elif x.column not in {c.name for c in tabelas[x.table].columns}:
            erros.append(
                f"external_references cita a coluna inexistente "
                f"'{x.table}.{x.column}'.")

    return erros


# --------------------------------------------------------- 4. Arquiteto de software
def validar_arquitetura(
    a: ModuleArchitecture, spec: FinalSpecification, modelo: ModuleDataModel,
    conv: Convencoes = PADRAO,
) -> List[str]:
    erros = []
    ids_req = _ids_requisitos(spec)
    tabelas = {e.table for e in modelo.entities}

    if not a.java_package.startswith(conv.pacote_base):
        erros.append(
            f"java_package '{a.java_package}' deve seguir '{conv.pacote_base}<module_name>'.")

    vistos = []
    for ep in a.endpoints:
        rotulo = f"Endpoint {ep.method} {ep.path}"
        if not ep.path.startswith(conv.prefixo_api):
            erros.append(f"{rotulo} deve começar com {conv.prefixo_api}.")
        else:
            segs = [s for s in ep.path[len(conv.prefixo_api):].split("/") if s]
            ruins = [s for s in segs if not _KEBAB_SEG.match(s)]
            if ruins:
                erros.append(f"{rotulo}: segmentos fora de kebab-case: {ruins}.")
        vistos.append(f"{ep.method} {ep.path}")

        desconhecidos = [r for r in ep.addressed_requirements if r not in ids_req]
        if desconhecidos:
            erros.append(f"{rotulo} cita requisitos inexistentes: {desconhecidos}.")
        if not ep.addressed_requirements:
            erros.append(f"{rotulo} não cita nenhum requisito.")

        fora = [t for t in ep.tables_touched if t not in tabelas]
        if fora:
            erros.append(
                f"{rotulo} usa tabelas fora do modelo de dados validado: {fora}. "
                "Não altere nem invente tabelas; use serviços públicos de outros módulos.")

        if ep.requires_authentication and not ep.roles:
            erros.append(f"{rotulo} exige autenticação mas não declara roles.")

    dup = _duplicados(vistos)
    if dup:
        erros.append(f"Endpoints duplicados: {dup}.")

    for d in a.architectural_decisions:
        citados = _ID_REQ_EM_TEXTO.findall(d.requirement)
        if not citados:
            erros.append(
                f"Decisão '{d.decision[:60]}' não cita um requisito (FRxx/NFRxx).")
        inexistentes = [r for r in citados if r not in ids_req]
        if inexistentes:
            erros.append(
                f"Decisão '{d.decision[:60]}' cita requisitos inexistentes: "
                f"{inexistentes}.")

    proprio = spec.module_name.strip().lower()
    for dep in a.module_dependencies:
        if dep.module.strip().lower() == proprio:
            erros.append("module_dependencies não pode conter o próprio módulo.")

    return erros


# --------------------------------------------------------- 5. Planejador
def _endpoint(texto: str) -> str:
    partes = texto.split()
    return f"{partes[0].upper()} {partes[1]}" if len(partes) == 2 else texto.strip()


def endpoints_da_arquitetura(a: ModuleArchitecture) -> Set[str]:
    return {f"{ep.method} {ep.path}" for ep in a.endpoints}


def _checar_caminhos(arquivos: List[PlannedFile], lado: str) -> List[str]:
    erros = []
    for f in arquivos:
        motivo = layout.caminho_invalido(f.path)
        if motivo:
            erros.append(f"{lado}: caminho '{f.path}' inválido: {motivo}.")
    dup = _duplicados([f.path for f in arquivos])
    if dup:
        erros.append(f"{lado}: caminhos repetidos no plano: {dup}.")
    return erros


def _checar_depends_on(arquivos: List[PlannedFile], lado: str,
                       base: Set[str]) -> List[str]:
    erros = []
    planejados = {f.path for f in arquivos}
    grafo: Dict[str, List[str]] = {}
    for f in arquivos:
        desconhecidos = [d for d in f.depends_on if d not in planejados | base]
        if desconhecidos:
            erros.append(
                f"{lado}: '{f.path}' depende de arquivos que não estão no plano nem "
                f"na base: {desconhecidos}.")
        grafo[f.path] = [d for d in f.depends_on if d in planejados]
    ciclo = _ciclo(grafo)
    if ciclo:
        erros.append(f"{lado}: dependência circular entre arquivos: {' -> '.join(ciclo)}.")
    return erros


def validar_plano(
    p: ImplementationPlan,
    nome: str,
    spec: FinalSpecification,
    modelo: ModuleDataModel,
    arq: ModuleArchitecture,
    ctx: ProjectContext,
    plano_base: Optional[ImplementationPlan] = None,
) -> List[str]:
    erros = []
    lay = layout.layout_modulo(ctx, nome, arq.java_package)
    ids_req = _ids_requisitos(spec)
    tabelas = {e.table for e in modelo.entities}
    endpoints = endpoints_da_arquitetura(arq)
    base_back = {f.path for f in plano_base.backend_files} if plano_base else set()
    base_front = {f.path for f in plano_base.frontend_files} if plano_base else set()

    if not p.backend_files:
        erros.append("backend_files está vazio.")
    erros += _checar_caminhos(p.backend_files, "backend")
    erros += _checar_caminhos(p.frontend_files, "frontend")

    for f in p.backend_files:
        if f.path.endswith(".sql") or f.path.startswith(layout.MIGRACOES_DIR):
            erros.append(f"backend: não planeje migrations ('{f.path}'); a migration "
                         "é gerada automaticamente a partir do modelo de dados.")
        elif not f.path.startswith(lay.backend_prefixos):
            erros.append(f"backend: '{f.path}' está fora da pasta do módulo "
                         f"{list(lay.backend_prefixos)}.")
        if f.path in base_back:
            erros.append(f"backend: '{f.path}' já pertence à base; não replaneje.")

    if layout.tem_frontend(ctx):
        for f in p.frontend_files:
            if not f.path.startswith(lay.frontend_prefixo):
                erros.append(f"frontend: '{f.path}' está fora de '{lay.frontend_prefixo}'.")
            if f.path in base_front:
                erros.append(f"frontend: '{f.path}' já pertence à base; não replaneje.")
        rotas = [f for f in p.frontend_files if layout.sem_extensao(f.path) == lay.rotas]
        if not rotas:
            erros.append(f"frontend: falta o arquivo de rotas do módulo "
                         f"'{lay.rotas}.<ext>' (kind 'routes'), que a base importa.")
    elif p.frontend_files or p.screens:
        erros.append("O PROJECT CONTEXT não tem frontend: frontend_files e screens "
                     "devem ficar vazios.")

    todos = list(p.backend_files) + list(p.frontend_files)
    for f in todos:
        fora = [t for t in f.tables if t not in tabelas]
        if fora:
            erros.append(f"'{f.path}' cita tabelas fora do modelo de dados: {fora}.")
        eps = [e for e in f.endpoints if _endpoint(e) not in endpoints]
        if eps:
            erros.append(f"'{f.path}' cita endpoints fora da arquitetura: {eps}. "
                         'Use "METHOD /path" exatamente como na arquitetura.')
        reqs = [r for r in f.requirements if r not in ids_req]
        if reqs:
            erros.append(f"'{f.path}' cita requisitos inexistentes: {reqs}.")

    erros += _checar_depends_on(p.backend_files, "backend", base_back)
    erros += _checar_depends_on(p.frontend_files, "frontend", base_front)

    entidades = {t for f in p.backend_files if f.kind == "entity" for t in f.tables}
    sem_entidade = sorted(tabelas - entidades)
    if sem_entidade:
        erros.append(f"Tabelas sem arquivo kind 'entity' que as mapeie: {sem_entidade}.")
    cobertos = {_endpoint(e) for f in p.backend_files if f.kind == "controller"
                for e in f.endpoints}
    sem_controller = sorted(endpoints - cobertos)
    if sem_controller:
        erros.append(f"Endpoints sem arquivo kind 'controller' que os implemente: "
                     f"{sem_controller}.")
    exportados = {x for f in p.backend_files for x in f.exports}
    for s in arq.public_services:
        if s.interface not in exportados:
            erros.append(f"Serviço público '{s.interface}' não aparece em exports de "
                         "nenhum arquivo do backend.")
    for ev in arq.domain_events:
        if ev.name not in exportados:
            erros.append(f"Evento '{ev.name}' não aparece em exports de nenhum arquivo "
                         "do backend.")

    paginas = {f.path for f in p.frontend_files if f.kind == "page"}
    consumidos = {_endpoint(e) for f in p.frontend_files if f.kind == "api_client"
                  for e in f.endpoints}
    dup = _duplicados([t.route for t in p.screens])
    if dup:
        erros.append(f"Rotas de tela repetidas: {dup}.")
    for t in p.screens:
        if t.page_file not in paginas:
            erros.append(f"Tela '{t.name}': page_file '{t.page_file}' não é um arquivo "
                         "kind 'page' do plano.")
        fora = [e for e in t.endpoints if _endpoint(e) not in endpoints]
        if fora:
            erros.append(f"Tela '{t.name}' usa endpoints fora da arquitetura: {fora}.")
        sem_cliente = [e for e in t.endpoints
                       if _endpoint(e) in endpoints and _endpoint(e) not in consumidos]
        if sem_cliente:
            erros.append(f"Tela '{t.name}' usa endpoints que nenhum arquivo kind "
                         f"'api_client' consome: {sem_cliente}.")
        reqs = [r for r in t.requirements if r not in ids_req]
        if reqs:
            erros.append(f"Tela '{t.name}' cita requisitos inexistentes: {reqs}.")
    return erros


def validar_plano_base(p: ImplementationPlan, ctx: ProjectContext,
                       modulos: List[str]) -> List[str]:
    erros = []
    conv = convencoes(ctx)
    proibidos = tuple(layout.prefixos_proibidos_base(ctx, conv.pacote_base, modulos))

    if not p.backend_files:
        erros.append("backend_files da base está vazio.")
    if layout.tem_frontend(ctx) and not p.frontend_files:
        erros.append("frontend_files da base está vazio, mas o PROJECT CONTEXT tem frontend.")
    if not layout.tem_frontend(ctx) and p.frontend_files:
        erros.append("O PROJECT CONTEXT não tem frontend: frontend_files deve ficar vazio.")
    if p.screens:
        erros.append("O plano da base não tem telas: screens deve ficar vazio.")
    if not p.conventions:
        erros.append("conventions está vazio: descreva as regras que os módulos "
                     "devem seguir (rotas, erros, tenant, cliente HTTP...).")

    erros += _checar_caminhos(p.backend_files, "backend")
    erros += _checar_caminhos(p.frontend_files, "frontend")
    for f in list(p.backend_files) + list(p.frontend_files):
        if f.path.startswith(proibidos):
            erros.append(f"'{f.path}' está numa pasta de módulo; a base só contém "
                         "código compartilhado.")
        if f.path.endswith(".sql") and not f.path.startswith(layout.PREFIXO_MIGRACAO_BASE):
            erros.append(f"Migration da base '{f.path}' deve começar com "
                         f"'{layout.PREFIXO_MIGRACAO_BASE}'.")
        if f.tables or f.endpoints:
            erros.append(f"'{f.path}': a base não tem tabelas nem endpoints de módulo.")
    erros += _checar_depends_on(p.backend_files, "backend", set())
    erros += _checar_depends_on(p.frontend_files, "frontend", set())
    return erros


# --------------------------------------------------------- 6/7. Implementadores
@dataclass
class Conhecidos:
    """O que o código gerado pode referenciar sem inventar."""
    caminhos: Set[str] = field(default_factory=set)       # arquivos do mesmo lado
    pacote_base: str = ""                                  # ex.: com.distributor.erp.
    tabelas: Optional[Set[str]] = None                     # None = não checa @Table
    endpoints: Set[str] = field(default_factory=set)       # "/api/v1/users/{}"
    rotas_modulos: Set[str] = field(default_factory=set)   # src/modules/x/routes

    @property
    def classes(self) -> Set[str]:
        return {c for c in (layout.fqcn(p) for p in self.caminhos) if c}


_PLACEHOLDER = re.compile(
    r"\bTODO\b|\bFIXME\b|^\s*(//|#|\*|<!--)\s*\.\.\.|"
    r"(your|seu|sua) (code|código|implementation|implementação) (here|aqui)",
    re.I | re.M)
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;?\s*$", re.M)
_IMPORT_JVM = re.compile(r"^\s*import\s+(static\s+)?([\w.]+(?:\.\*)?)\s*;?\s*$", re.M)
_TABLE = re.compile(r"@Table\s*\(\s*name\s*=\s*\"([^\"]+)\"")
_IMPORT_FRONT = re.compile(
    r"""(?:^|\s)(?:import|export)\s[^'"`;]*?from\s*['"]([^'"]+)['"]"""
    r"""|^\s*import\s*['"]([^'"]+)['"]"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)""", re.M)
_URL_API = re.compile(r"""['"`](/api/v\d+[^'"`\s?#]*)""")
_SO_PREFIXO = re.compile(r"^/api/v\d+$")


def normalizar_rota(caminho: str) -> str:
    c = re.sub(r"\$\{[^}]*\}", "{}", caminho)
    c = re.sub(r"\{[^}]*\}", "{}", c)
    c = re.sub(r"/:[A-Za-z_]\w*", "/{}", c)
    return c.rstrip("/") or "/"


def _checar_jvm(caminho: str, texto: str, k: Conhecidos) -> List[str]:
    erros = []
    esperado = layout.fqcn(caminho)
    if esperado:
        pacote_esperado, _, nome = esperado.rpartition(".")
        m = _PACKAGE.search(texto)
        if not m or m.group(1) != pacote_esperado:
            erros.append(f"'{caminho}': declare 'package {pacote_esperado};' "
                         "(o pacote deve corresponder à pasta).")
        if not re.search(rf"\b(class|interface|enum|record|object)\s+{re.escape(nome)}\b",
                         texto):
            erros.append(f"'{caminho}': o tipo principal deve se chamar '{nome}'.")
    classes = k.classes
    pacotes = {c.rpartition(".")[0] for c in classes}
    for estatico, imp in _IMPORT_JVM.findall(texto):
        if not k.pacote_base or not imp.startswith(k.pacote_base):
            continue
        if imp.endswith(".*"):
            alvo = imp[:-2]
            ok = alvo in pacotes or alvo in classes
        else:
            partes = imp.split(".")
            # aceita classe, classe aninhada e membro estático
            ok = any(".".join(partes[:i]) in classes for i in range(len(partes), 1, -1))
        if not ok:
            erros.append(f"'{caminho}' importa '{imp}', que não existe no plano, na "
                         "base nem nos módulos já gerados.")
    if k.tabelas is not None:
        for t in _TABLE.findall(texto):
            if t not in k.tabelas:
                erros.append(f"'{caminho}' mapeia a tabela '{t}', que não está no "
                             "modelo de dados do módulo.")
    return erros


def _resolve(origem: str, especificador: str, k: Conhecidos) -> bool:
    if especificador.startswith("@/"):
        alvo = posixpath.normpath("src/" + especificador[2:])
    elif especificador.startswith("."):
        alvo = posixpath.normpath(posixpath.join(posixpath.dirname(origem), especificador))
    else:
        return True  # pacote npm: não checado aqui
    extensoes = layout.EXTENSOES_FRONTEND + (".css", ".scss", ".json")
    candidatos = ({alvo} | {alvo + e for e in extensoes}
                  | {f"{alvo}/index{e}" for e in layout.EXTENSOES_FRONTEND})
    if candidatos & k.caminhos:
        return True
    return layout.sem_extensao(alvo) in k.rotas_modulos


def _checar_front(caminho: str, texto: str, k: Conhecidos) -> List[str]:
    erros = []
    for grupos in _IMPORT_FRONT.findall(texto):
        esp = next((g for g in grupos if g), None)
        if esp and not _resolve(caminho, esp, k):
            erros.append(f"'{caminho}' importa '{esp}', que não existe no plano nem na base.")
    for url in _URL_API.findall(texto):
        rota = normalizar_rota(url)
        if _SO_PREFIXO.match(rota):
            continue
        if rota not in k.endpoints:
            erros.append(f"'{caminho}' chama '{url}', que não é endpoint da arquitetura.")
    return erros


def validar_arquivos(g: GeneratedFiles, pedidos: List[PlannedFile],
                     k: Conhecidos) -> List[str]:
    erros = []
    esperados = [f.path for f in pedidos]
    recebidos = [f.path for f in g.files]
    faltando = [p for p in esperados if p not in recebidos]
    extras = [p for p in recebidos if p not in esperados]
    if faltando:
        erros.append(f"Arquivos pedidos e não entregues: {faltando}.")
    if extras:
        erros.append(f"Arquivos entregues que não foram pedidos neste lote: {extras}.")
    dup = _duplicados(recebidos)
    if dup:
        erros.append(f"Arquivos entregues em duplicidade: {dup}.")

    for f in g.files:
        if f.path not in esperados:
            continue
        texto = layout.limpar_conteudo(f.content)
        if not texto.strip():
            erros.append(f"'{f.path}' está vazio.")
            continue
        if _PLACEHOLDER.search(texto):
            erros.append(f"'{f.path}' tem código incompleto (TODO, FIXME ou '...'). "
                         "Escreva a implementação completa.")
        if f.path.endswith((".java", ".kt")):
            erros += _checar_jvm(f.path, texto, k)
        elif f.path.endswith(layout.EXTENSOES_FRONTEND):
            erros += _checar_front(f.path, texto, k)
    return erros
