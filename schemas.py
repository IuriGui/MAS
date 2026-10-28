"""Contratos (saída estruturada) entre os agentes do pipeline.

Arquiteto-chefe     -> ProjectContext (uma vez por projeto)
Requisitos          -> ModuleAnalysis
Esclarecedor        -> FinalSpecification
Modelador de dados  -> ModuleDataModel
Arquiteto software  -> ModuleArchitecture
Planejador          -> ImplementationPlan (junção modelo de dados + arquitetura)
Implementadores     -> GeneratedFiles (um lote de arquivos por chamada)
Build (correção)    -> BuildFix (edições por trecho nos arquivos do próprio agente)

Comunicação entre agentes (F3):
Consulta            -> ConsultationAnswer (o dono de um artefato responde a uma pergunta)
Mensagens           -> AgentMessage (registro em mensagens.jsonl)
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high"]
Trust = Literal["high", "medium", "low"]
ProblemType = Literal[
    "ambiguity", "gap", "conflict", "duplication", "inconsistency"
]


# ------------------------------------------------------ 0. Arquiteto-chefe
class TechItem(BaseModel):
    name: str = Field(description='Technology name, or "None" when deliberately not used')
    version: str = Field(description='Major version only, or "0" when not used')
    verify_version: bool
    justification: str = Field(description="ONE sentence")


class TechStack(BaseModel):
    language: TechItem
    framework: TechItem
    database: TechItem
    cache: TechItem
    message_broker: TechItem
    authentication_mechanism: TechItem
    frontend: TechItem


class MultiTenancy(BaseModel):
    enabled: bool
    strategy: str
    details: str


class AuthDecision(BaseModel):
    token_type: str
    rbac_enforcement: str


class ErrorConventions(BaseModel):
    format: str
    status_code_conventions: str


class ObservabilityDecision(BaseModel):
    logging: str
    audit: str
    observability: str


class ConfigDecision(BaseModel):
    strategy: str


class DataConventions(BaseModel):
    id_type: str = Field(description="e.g.: UUIDv7, BIGINT")
    timestamps_and_timezone: str
    soft_delete: str
    money_and_decimals: str


class ApiConventions(BaseModel):
    style: str
    versioning: str = Field(description="e.g.: URI path versioning starting at /api/v1")
    pagination: str


class CrossCuttingDecisions(BaseModel):
    multi_tenancy: MultiTenancy
    authentication_and_authorization: AuthDecision
    error_format_and_status_codes: ErrorConventions
    logging_audit_and_observability: ObservabilityDecision
    configuration_and_secrets: ConfigDecision
    data_conventions: DataConventions
    api_conventions: ApiConventions


class NamingConventions(BaseModel):
    tables: str
    columns: str
    endpoints: str
    events: str
    packages: str = Field(description="e.g.: com.company.product.<module_name>")


class CommunicationMechanism(BaseModel):
    allowed: bool
    when_to_use: str
    delivery_guarantee: str


class InterModuleCommunication(BaseModel):
    direct_call: CommunicationMechanism
    events: CommunicationMechanism
    queue: CommunicationMechanism


class ModuleMapEntry(BaseModel):
    name: str = Field(description="Module name exactly as declared in the input")
    kind: Literal["business", "platform"]
    responsibility: str
    depends_on: List[str] = Field(description="Names of other modules of this map")
    exposed_capabilities: List[str] = Field(
        description="Short plain-language phrases; no class or method names")


class ArchitectureDecisionRecord(BaseModel):
    id: str = Field(description="ADR01, ADR02...")
    context: str
    options_considered: List[str]
    decision: str
    consequences: str


class ProjectRisk(BaseModel):
    risk: str
    severity: Severity
    mitigation: str


class ProjectAssumption(BaseModel):
    id: str = Field(description="AS01, AS02...")
    decision: str
    reason: str
    reversible: bool
    impact_if_wrong: Severity


class ProjectContext(BaseModel):
    context_version: str
    architectural_style: Literal["monolith", "modular_monolith", "microservices"]
    tech_stack: TechStack
    cross_cutting_decisions: CrossCuttingDecisions
    naming_conventions: NamingConventions
    inter_module_communication: InterModuleCommunication
    module_map: List[ModuleMapEntry]
    build_order: List[str] = Field(
        description="Every module of module_map, each dependency before its dependents")
    decisions: List[ArchitectureDecisionRecord]
    risks: List[ProjectRisk]
    assumptions: List[ProjectAssumption]


# ----------------------------------------------------------------- comuns
class Requirement(BaseModel):
    id: str = Field(description="FR01, FR02... ou NFR01, NFR02...")
    description: str = Field(description="Requisito específico, verificável e testável")


class CrossModuleDependency(BaseModel):
    module: str
    nature: str = Field(description="e.g., data lookup, event, validation")
    reason: str


# ------------------------------------------------------ 1. Requisitos
class Problem(BaseModel):
    id: str = Field(description="PROB01, PROB02...")
    title: str
    type: ProblemType
    severity: Severity
    description: str
    correction_suggestion: str = Field(
        description="Concrete suggestion for the Clarifier"
    )


class ModuleAnalysis(BaseModel):
    module_name: str
    scope_summary: str
    problems: List[Problem]
    functional_requirements: List[Requirement]
    non_functional_requirements: List[Requirement]
    dependencies: List[CrossModuleDependency]
    observations: List[str]


# ------------------------------------------------------ 2. Esclarecedor

class ResolvedProblem(BaseModel):
    id: str = Field(description="Original problem ID (PROBxx)")
    title: str
    type: ProblemType
    severity: Severity
    problem: str
    adopted_decision: str
    justification: str
    confidence: Trust
    requirements_impact: str

class FinalSpecification(BaseModel):
    module_name: str
    final_scope: str
    resolved_problems: List[ResolvedProblem]
    functional_requirements: List[Requirement]
    non_functional_requirements: List[Requirement]
    interdependencies: List[CrossModuleDependency]
    review_alerts: List[str] = Field(
        description="Low-confidence problems and relevant business decisions"
    )
    consolidated_specification: str


# ------------------------------------------------------ 3. Modelador de dados
class Column(BaseModel):
    name: str = Field(description="snake_case, singular")
    sql_type: str = Field(
        description="PostgreSQL type, e.g.: UUID, VARCHAR(120), "
                    "TIMESTAMP WITH TIME ZONE, DECIMAL(19,4)"
    )
    required: bool = Field(description="true = NOT NULL")
    primary_key: bool
    reference: Optional[str] = Field(
        description="FK in table.column format, ONLY to a table of this same "
                    "module; null otherwise (cross-module ids are logical, no FK)"
    )
    constraints: List[str] = Field(
        description="Column-level constraints, e.g.: UNIQUE, DEFAULT now(), "
                    "CHECK (status IN ('ACTIVE','BLOCKED'))"
    )


class Index(BaseModel):
    name: str = Field(description="e.g.: idx_users_tenant_id_email")
    columns: List[str] = Field(description="Columns of this table, in index order")
    unique: bool
    where: Optional[str] = Field(
        description="Partial index predicate, e.g.: deleted_at IS NULL; or null"
    )
    requirement: str = Field(
        description="Existing FRxx/NFRxx ID that justifies this index"
    )


class Entity(BaseModel):
    table: str = Field(description="snake_case and plural")
    purpose: str
    columns: List[Column]
    indexes: List[Index]
    constraints: List[str] = Field(
        description="Table-level constraints: composite UNIQUE, CHECK, etc."
    )


class Relationship(BaseModel):
    source: str = Field(description="Table of this module")
    target: str = Field(description="Table of this module")
    cardinality: Literal["1:1", "1:N", "N:N"]
    via: Optional[str] = Field(
        description="Join table for N:N (must be one of the entities) or the "
                    "FK column (table.column) for 1:1 / 1:N"
    )
    description: str


class ExternalReference(BaseModel):
    table: str
    column: str = Field(description="Logical id column, e.g.: customer_id")
    module: str = Field(description="Owner module of the referenced data")
    reason: str


class ModuleDataModel(BaseModel):
    module_name: str
    entities: List[Entity]
    relationships: List[Relationship]
    external_references: List[ExternalReference] = Field(
        description="Logical (non-FK) ids that point to other modules' data"
    )
    integrity_rules: List[str] = Field(
        description="Uniqueness, state transitions, CHECKs, invariants"
    )
    assumptions: List[str] = Field(
        description="Technical parameters introduced that no requirement defines"
    )
    data_model_summary: str


# ------------------------------------------------------ 4. Arquiteto de software
class Endpoint(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(description="Must start with the API version prefix of the "
                                  "PROJECT CONTEXT, e.g. /api/v1/ (kebab-case plural nouns)")
    purpose: str
    requires_authentication: bool
    roles: List[str]
    request_summary: str
    response_summary: str
    http_status_codes: List[int]
    tables_touched: List[str] = Field(
        description="Tables of the provided data model read/written by this endpoint"
    )
    addressed_requirements: List[str] = Field(
        description="Existing FRxx/NFRxx IDs"
    )


class PublicService(BaseModel):
    interface: str
    method: str
    parameters: str
    return_type: str
    purpose: str
    consumer_module: Optional[str] = Field(
        description="Consumer module or null"
    )


class DomainEvent(BaseModel):
    name: str = Field(description="PascalCasePastTense, e.g.: UserBlocked")
    payload: str
    trigger: str = Field(description="What causes the event to be published")
    consumers: List[str] = Field(description="Known consumer modules")
    uses_outbox: bool


class ModuleDependency(BaseModel):
    module: str
    operation: str
    public_interface: str
    reason: str
    mechanism: Literal["direct_call", "event"]
    direction: str = Field(
        description="Who depends on whom, e.g.: this -> other"
    )


class ArchitecturalDecision(BaseModel):
    topic: Literal[
        "transactions", "locking", "caching", "security", "sessions",
        "validation", "events", "pagination", "other"
    ]
    decision: str
    reason: str
    requirement: str = Field(description="Existing FRxx/NFRxx ID(s)")


class ModuleArchitecture(BaseModel):
    module_name: str
    java_package: str = Field(
        description="PROJECT CONTEXT naming_conventions.packages with <module_name> filled")
    package_structure: List[str]
    endpoints: List[Endpoint]
    public_services: List[PublicService]
    domain_events: List[DomainEvent]
    module_dependencies: List[ModuleDependency]
    architectural_decisions: List[ArchitecturalDecision]
    architecture_summary: str


# ------------------------------------------------------ 5. Planejador de implementação
FileKind = Literal[
    # backend
    "build", "config", "entrypoint", "entity", "repository", "service",
    "controller", "dto", "mapper", "public_api", "event", "listener",
    "security", "exception", "test",
    # frontend
    "page", "component", "api_client", "types", "hook", "routes", "store",
    "style",
    "other",
]


class PlannedFile(BaseModel):
    path: str = Field(description="Relative to the backend or frontend root")
    kind: FileKind
    purpose: str
    exports: List[str] = Field(
        description="Public names defined by the file (classes, interfaces, "
                    "functions, components, types), exactly as they will be written")
    depends_on: List[str] = Field(
        description="Paths of planned or base files this file imports")
    tables: List[str] = Field(description="Data model tables mapped/read/written")
    endpoints: List[str] = Field(
        description='Endpoints implemented or called, "METHOD /path" exactly as '
                    "in the architecture")
    requirements: List[str] = Field(description="FRxx/NFRxx IDs implemented")


class Screen(BaseModel):
    name: str
    route: str = Field(description="Frontend route, e.g. /users/:id")
    purpose: str
    roles: List[str]
    endpoints: List[str] = Field(description='"METHOD /path" used by the screen')
    requirements: List[str]
    page_file: str = Field(description="Path of the planned frontend page file")


class ImplementationPlan(BaseModel):
    module_name: str
    backend_files: List[PlannedFile]
    frontend_files: List[PlannedFile]
    screens: List[Screen]
    conventions: List[str] = Field(
        description="BASE plan only: rules every module file must follow "
                    "(how to register routes, throw errors, read the tenant...)")
    implementation_notes: List[str] = Field(
        description="Gaps between data model and architecture and how they are handled")


# ------------------------------------------------------ 6/7. Implementadores
class GeneratedFile(BaseModel):
    path: str = Field(description="Exactly one of the requested paths")
    content: str = Field(description="Complete file content, no markdown fences")


class GeneratedFiles(BaseModel):
    files: List[GeneratedFile]
    notes: List[str] = Field(description="Decisions or limitations worth recording")


# ------------------------------------------------------ Comunicação (F3)
MessageKind = Literal["question", "answer", "blocker", "resolution", "unresolved",
                      "build_failure", "change_request", "declined"]


class AgentMessage(BaseModel):
    """Mensagem entre agentes, registrada pelo orquestrador em mensagens.jsonl."""
    id: str
    sender: str = Field(description="Etapa de quem envia (ou 'orquestrador')")
    recipient: str = Field(description="Etapa de quem recebe (ou 'revisao_humana')")
    kind: MessageKind
    module: str
    recipient_module: Optional[str] = Field(
        default=None, description="Módulo de quem recebe, quando não é `module` "
                                  "(pedido de correção a outro módulo ou à base)")
    content: str
    in_reply_to: Optional[str] = None
    sent_at: str


class ConsultationAnswer(BaseModel):
    answer: str = Field(description="Direct answer to the question, based on your artifact")
    defect_in_my_artifact: bool = Field(
        description="True if the question revealed a defect (gap, contradiction) in "
                    "your own artifact")
    defect_description: str = Field(
        default="", description="What is wrong in your artifact, if anything")


# ------------------------------------------------------ Build (correção)
class CodeEdit(BaseModel):
    path: str = Field(description="File to edit, relative to the backend or frontend root")
    old_text: str = Field(
        description="Exact text to replace, copied from the CURRENT file without the "
                    "line-number prefixes. It must occur exactly once in the file: "
                    "include enough surrounding lines to make it unique")
    new_text: str = Field(description="Replacement text (empty to delete old_text)")


class BuildFix(BaseModel):
    edits: List[CodeEdit] = Field(
        description="Edits applied in order; several edits may target the same file")
    reply: str = Field(
        default="", description="Only when answering a change request: what you changed, "
                                "or why you declined")
    notes: List[str] = Field(default_factory=list,
                             description="Decisions worth recording")
