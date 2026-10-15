"""Pipeline de UM módulo, como um grafo LangGraph:
   Requisitos -> Esclarecedor -> Modelador de dados -> Arquiteto de software
   -> Planejador -> Implementação (backend, frontend)
   com uma aresta condicional de cada etapa para a Revisão (F3).

Todos os agentes recebem o PROJECT CONTEXT (saída do Arquiteto-chefe). Sem
contexto explícito, usa o arquivo de CONTEXTO_PADRAO (config.py).

Cada etapa:
  1. invoca o agente (saída estruturada Pydantic);
  2. valida em código (validation.py);
  3. se falhar, re-executa com os erros anexados (até MAX_TENTATIVAS);
  4. publica o artefato aprovado no ambiente compartilhado (ambiente.py),
     em saida/<modulo>/, antes de seguir.

Comunicação entre agentes (F3), registrada em mensagens.jsonl:
  - consulta: um agente pergunta (ask_agent) ao dono de um artefato anterior;
    o dono responde em modo consulta (agents/consultor.py) e o fluxo segue;
  - bloqueio: um agente reporta (report_blocker) um defeito num artefato
    anterior. O grafo vai para a Revisão: o dono revisa o artefato com o pedido,
    e as etapas entre ele e quem bloqueou rodam de novo. Até MAX_REVISOES_MODULO
    revisões por módulo; depois disso, ou se a revisão não passa na validação, o
    bloqueio vira pendência para revisão humana e o módulo segue.

As etapas de implementação estão em implementacao.py. O código gerado vai para
<codigo_dir>/backend e <codigo_dir>/frontend.
"""
import json
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import get_current_run_tree, traceable

import implementacao
import layout
import validation
from agents import ferramentas
from ambiente import ARTEFATOS, Ambiente
from config import (COMUNICACAO, CONTEXTO_PADRAO, MAX_CONSULTAS_AGENTE,
                    MAX_REVISOES_MODULO, MAX_TENTATIVAS, SAIDA_DIR)
from execucao import PipelineError, executar_agente
from layout import slugify
from render import renderizar_relatorio
from schemas import ImplementationPlan, ProjectContext
from system_prompts import templates

ETAPAS = ("requisitos", "esclarecedor", "modelador_dados", "arquiteto_software",
          "planejador", "implementador_backend", "implementador_frontend")
ETAPAS_ESPECIFICACAO = ETAPAS[:4]

# nós do grafo, na ordem normal (a Revisão fica fora da ordem)
NOS = ETAPAS_ESPECIFICACAO + ("planejador", "implementacao")

# artefato -> etapas que o consomem
CONSUMIDORES = {
    "analise": ("esclarecedor",),
    "especificacao": ETAPAS[2:],
    "modelo_dados": ("arquiteto_software", "planejador", "implementador_backend"),
    "arquitetura": ETAPAS[4:],
    "plano": ETAPAS[5:],
}

# compatibilidade: estes nomes eram definidos aqui
_executar = executar_agente
__all__ = ["ETAPAS", "PipelineError", "processar_modulo", "carregar_contexto",
           "slugify", "_executar"]


def carregar_contexto(caminho: str = CONTEXTO_PADRAO) -> ProjectContext:
    return ProjectContext.model_validate_json(Path(caminho).read_text(encoding="utf-8"))


def criar_agentes(contexto: ProjectContext) -> dict:
    from agents import (arquiteto_software, esclarecedor, implementador_backend,
                        implementador_frontend, modelador_de_dados, planejador,
                        requisitos)
    return {
        "requisitos": requisitos.get_agente(contexto),
        "esclarecedor": esclarecedor.get_agente(contexto),
        "modelador_dados": modelador_de_dados.get_agente(contexto),
        "arquiteto_software": arquiteto_software.get_agente(contexto),
        "planejador": planejador.get_agente(contexto),
        "implementador_backend": implementador_backend.get_agente(contexto),
        "implementador_frontend": implementador_frontend.get_agente(contexto),
    }


def _caminhos(manifesto) -> list:
    return [m["path"] for m in manifesto if "path" in m]


def lado_final(ate: str) -> Optional[str]:
    """Até que lado a base precisa estar implementada para rodar até `ate`."""
    if ate == "implementador_frontend":
        return "frontend"
    if ate == "implementador_backend":
        return "backend"
    return None


# ------------------------------------------------------------- grafo
class EstadoModulo(TypedDict, total=False):
    proxima: str                # próximo nó
    forcadas: List[str]         # etapas que rodam de novo depois de uma revisão
    pedido: Optional[dict]      # bloqueio em atendimento: destino, remetente, problemas
    revisoes: int               # revisões já feitas no módulo


def seguinte(no: str) -> str:
    i = NOS.index(no)
    return NOS[i + 1] if i + 1 < len(NOS) else END


def montar_grafo(nos: Dict[str, Callable[[EstadoModulo], dict]]):
    """Topologia do módulo. Cada nó devolve o estado com `proxima`."""
    g = StateGraph(EstadoModulo)
    for nome, funcao in nos.items():
        g.add_node(nome, funcao)
    g.add_edge(START, NOS[0])
    for no in ETAPAS_ESPECIFICACAO + ("planejador",):
        # só vai para a Revisão quem pode reportar bloqueio (o Requisitos não pode)
        destinos = [seguinte(no)] + (["revisao"] if no in ferramentas.BLOQUEIO else [])
        g.add_conditional_edges(no, lambda st: st["proxima"], destinos)
    g.add_conditional_edges("implementacao", lambda st: st["proxima"], [END])
    # depois de revisar, volta para a etapa seguinte ao dono do artefato (ou, se a
    # revisão não resolve, para a seguinte a quem bloqueou), ou escala de novo
    g.add_conditional_edges("revisao", lambda st: st["proxima"], list(NOS[1:]) + ["revisao"])
    return g.compile()


def diagrama_mermaid() -> str:
    """Diagrama do grafo do módulo (para a documentação)."""
    vazio = lambda st: {}  # noqa: E731
    return montar_grafo({n: vazio for n in NOS + ("revisao",)}).get_graph().draw_mermaid()


@traceable(name="pipeline_modulo", run_type="chain")
def processar_modulo(
    nome: str,
    descricao: str,
    outros_modulos: Optional[str] = None,
    ate: str = ETAPAS[-1],
    agentes: Optional[dict] = None,
    saida_dir: str = SAIDA_DIR,
    max_tentativas: int = MAX_TENTATIVAS,
    contexto: Optional[ProjectContext] = None,
    de: str = ETAPAS[0],
    plano_base: Optional[ImplementationPlan] = None,
    codigo_dir: Optional[str] = None,
    numero_migracao: int = 1,
    endpoints_externos: Optional[Set[str]] = None,
    ambiente: Optional[Ambiente] = None,
    max_revisoes: int = MAX_REVISOES_MODULO,
) -> dict:
    """Roda o pipeline de um módulo e devolve os artefatos produzidos.

    - `outros_modulos=None` gera o resumo dos outros módulos a partir do contexto.
    - `de`: retoma a partir desta etapa, lendo de saida/<modulo>/ os artefatos
      das etapas anteriores (evita refazer chamadas já pagas).
    - `plano_base`: base compartilhada já pronta (modo projeto). Sem ela, a base
      é gerada em saida/<modulo>/base/ quando alguma etapa de implementação roda.
    - `codigo_dir`: onde gravar o código (padrão: saida/<modulo>/codigo).
    - `ambiente`: ambiente compartilhado do projeto. Sem ele, cria um só para
      este módulo (saida/<modulo>/, com base e código dentro dessa pasta).
    - `max_revisoes`: bloqueios atendidos por módulo (F3).
    - `agentes` permite injetar agentes falsos em testes. Consultores falsos
      entram com a chave "consulta:<etapa>".
    O resultado traz `pendencias`: bloqueios que ficaram sem resolução.
    """
    for nome_param, valor in (("ate", ate), ("de", de)):
        if valor not in ETAPAS:
            raise ValueError(f"'{nome_param}' deve ser um de {ETAPAS}")
    if ETAPAS.index(de) > ETAPAS.index(ate):
        raise ValueError("'de' não pode vir depois de 'ate'")
    if contexto is None:
        contexto = carregar_contexto()
    if outros_modulos is None:
        outros_modulos = templates.resumo_outros_modulos(contexto, nome)

    rt = get_current_run_tree()
    if rt:
        rt.add_metadata({"modulo": nome, "ate": ate, "de": de})
        rt.add_tags([f"modulo:{slugify(nome)}"])

    if agentes is None:
        agentes = criar_agentes(contexto)

    if ambiente is None:
        pasta = Path(saida_dir) / slugify(nome)
        ambiente = Ambiente(Path(saida_dir), contexto,
                            codigo=Path(codigo_dir) if codigo_dir else pasta / "codigo",
                            base=pasta / "base", registro=pasta / "ambiente.jsonl")
    pasta = ambiente.pasta_modulo(nome)
    metricas: dict = {}
    if de != ETAPAS[0] and (pasta / "metricas.json").exists():
        metricas = json.loads((pasta / "metricas.json").read_text())

    execucao = _Modulo(
        nome=nome, descricao=descricao, outros=outros_modulos, ate=ate, de=de,
        agentes=agentes, amb=ambiente, contexto=contexto, metricas=metricas,
        max_tentativas=max_tentativas, plano_base=plano_base,
        numero_migracao=numero_migracao, endpoints_externos=endpoints_externos,
        max_revisoes=max_revisoes)
    try:
        return execucao.rodar()
    finally:
        # grava métricas mesmo se uma etapa falhar
        (pasta / "metricas.json").write_text(json.dumps(metricas, indent=2))


class _Modulo:
    """Estado e nós do grafo de um módulo."""

    def __init__(self, *, nome, descricao, outros, ate, de, agentes, amb, contexto,
                 metricas, max_tentativas, plano_base, numero_migracao,
                 endpoints_externos, max_revisoes):
        self.nome, self.descricao, self.outros = nome, descricao, outros
        self.ate, self.de = ate, de
        self.agentes, self.amb, self.contexto = agentes, amb, contexto
        self.metricas, self.max_tentativas = metricas, max_tentativas
        self.plano_base, self.numero_migracao = plano_base, numero_migracao
        self.endpoints_externos = endpoints_externos
        self.max_revisoes = max_revisoes
        self.pasta = amb.pasta_modulo(nome)
        self.codigo = amb.codigo
        self.conv = validation.convencoes(contexto)
        self.artefatos: dict = {}
        self.pendencias: List[dict] = []
        self._consultores: dict = {}
        self._carregar_anteriores()

    # --------------------------------------------------------- base
    def roda(self, etapa: str) -> bool:
        return ETAPAS.index(self.de) <= ETAPAS.index(etapa) <= ETAPAS.index(self.ate)

    def _carregar_anteriores(self) -> None:
        """Artefatos das etapas anteriores a `de` são lidos do ambiente."""
        for etapa in ETAPAS[:ETAPAS.index(self.de)]:
            if etapa not in ARTEFATOS:
                continue
            chave = ARTEFATOS[etapa][0]
            obj = self.amb.artefato(self.nome, chave)
            if obj is not None:
                self.artefatos[chave] = obj
            elif any(self.roda(e) for e in CONSUMIDORES[chave]):
                raise PipelineError(
                    f"Para começar em '{self.de}' é preciso "
                    f"{self.amb.caminho_artefato(self.nome, chave)} (etapa '{etapa}').")

    def falhar(self, rotulo, erros, obj):
        (self.pasta / f"{rotulo}.invalido.json").write_text(
            obj.model_dump_json(indent=2), encoding="utf-8")
        (self.pasta / "metricas.json").write_text(json.dumps(self.metricas, indent=2))
        raise PipelineError(
            f"Etapa '{rotulo}' não passou na validação após {self.max_tentativas} "
            f"tentativas:\n- " + "\n- ".join(erros)
            + f"\nÚltima saída em {self.pasta}/{rotulo}.invalido.json"
        )

    # Mensagem e validação de cada etapa de especificação. São montadas na hora:
    # depois de uma revisão, usam os artefatos revisados.
    def mensagem(self, etapa: str) -> str:
        a, n, o = self.artefatos, self.nome, self.outros
        if etapa == "requisitos":
            return templates.msg_requisitos(n, self.descricao, o)
        if etapa == "esclarecedor":
            return templates.msg_esclarecedor(n, self.descricao, a["analise"], o)
        if etapa == "modelador_dados":
            return templates.msg_modelador_dados(n, a["especificacao"], o)
        return templates.msg_arquiteto_software(n, a["especificacao"], a["modelo_dados"], o)

    def validador(self, etapa: str):
        a, conv = self.artefatos, self.conv
        if etapa == "requisitos":
            return validation.validar_analise
        if etapa == "esclarecedor":
            analise = a["analise"]
            return lambda e: validation.validar_especificacao(e, analise)
        spec = a["especificacao"]
        if etapa == "modelador_dados":
            return lambda m: validation.validar_modelo_dados(m, spec, conv)
        modelo = a["modelo_dados"]
        return lambda x: validation.validar_arquitetura(x, spec, modelo, conv)

    def etapa_estruturada(self, etapa, mensagem, validar, canal=None, rotulo=None):
        rotulo = rotulo or etapa
        print(f"[{ETAPAS.index(etapa) + 1}/{ETAPAS.index(self.ate) + 1}] {rotulo} — "
              f"{self.nome}")
        obj, erros = executar_agente(rotulo, self.agentes[etapa], mensagem, validar,
                                     self.metricas, self.max_tentativas, modulo=self.nome,
                                     leitura=self.amb.leitura(), canal=canal)
        if erros:
            self.falhar(rotulo, erros, obj)
        self.amb.publicar(self.nome, etapa, obj, autor=rotulo)
        self.artefatos[ARTEFATOS[etapa][0]] = obj
        return obj

    # --------------------------------------------------------- comunicação
    def canal(self, etapa: str) -> Optional[ferramentas.Canal]:
        if not COMUNICACAO:
            return None
        return ferramentas.Canal(
            etapa=etapa,
            consultar=lambda destino, pergunta: self.consultar(etapa, destino, pergunta),
            bloquear=lambda destino, problema: self.amb.enviar(
                etapa, destino, "blocker", self.nome, problema),
            max_consultas=MAX_CONSULTAS_AGENTE)

    def consultor(self, etapa: str):
        if f"consulta:{etapa}" in self.agentes:
            return self.agentes[f"consulta:{etapa}"]
        if etapa not in self._consultores:
            from agents import consultor
            self._consultores[etapa] = consultor.get_agente(etapa, self.contexto)
        return self._consultores[etapa]

    def consultar(self, remetente: str, destino: str, pergunta: str) -> str:
        """Leva a pergunta ao dono do artefato e devolve a resposta dele."""
        q = self.amb.enviar(remetente, destino, "question", self.nome, pergunta)
        print(f"  [{remetente}] pergunta ao {destino}: {pergunta[:100]}")
        papel = ferramentas.PAPEIS[destino]
        artefato = self.artefatos.get(ARTEFATOS[destino][0])
        if artefato is None:
            resposta = f"The {papel} has not published its artifact yet."
        else:
            try:
                r, _ = executar_agente(
                    f"{destino}_consulta", self.consultor(destino),
                    templates.msg_consulta(self.nome, ferramentas.PAPEIS[remetente],
                                           artefato, pergunta),
                    lambda r: [], self.metricas, 1, modulo=self.nome,
                    leitura=self.amb.leitura())
                resposta = r.answer
                if r.defect_in_my_artifact:
                    resposta += (f"\n\nThe {papel} acknowledges a defect in its artifact: "
                                 f"{r.defect_description}")
                    if remetente in ferramentas.BLOQUEIO:
                        resposta += "\nIf it prevents you from doing your job, call report_blocker."
            except PipelineError:
                resposta = f"The {papel} could not answer this question."
        self.amb.enviar(destino, remetente, "answer", self.nome, resposta, em_resposta_a=q)
        return resposta

    def pendente(self, remetente: str, destino: str, problemas: List[str], motivo: str):
        texto = (f"{remetente} bloqueado por {destino} ({motivo}): " + "; ".join(problemas))
        self.amb.enviar("orquestrador", "revisao_humana", "unresolved", self.nome, texto)
        self.pendencias.append({"remetente": remetente, "destino": destino,
                                "problemas": problemas, "motivo": motivo})
        print(f"  PENDÊNCIA: {texto}")

    def escalonar(self, st: EstadoModulo, remetente: str, bloqueios) -> dict:
        """Decide o que fazer com os bloqueios de uma etapa: atende o do artefato
        mais acima (os outros voltam, se ainda valerem, quando a etapa rodar de novo)."""
        destino = min({d for d, _ in bloqueios}, key=ETAPAS.index)
        problemas = [p for d, p in bloqueios if d == destino]
        if st.get("revisoes", 0) >= self.max_revisoes:
            self.pendente(remetente, destino, problemas, "limite de revisões do módulo")
            return {"proxima": seguinte(remetente)}
        print(f"  [{remetente}] bloqueio: pede revisão ao {destino}")
        return {"proxima": "revisao",
                "pedido": {"destino": destino, "remetente": remetente, "problemas": problemas}}

    # --------------------------------------------------------- nós
    def no_especificacao(self, etapa: str):
        def executar(st: EstadoModulo) -> dict:
            forcada = etapa in st.get("forcadas", [])
            forcadas = [x for x in st.get("forcadas", []) if x != etapa]
            if self.roda(etapa) or forcada:
                canal = self.canal(etapa)
                self.etapa_estruturada(etapa, self.mensagem(etapa), self.validador(etapa),
                                       canal)
                if canal and canal.bloqueios:
                    return {**self.escalonar(st, etapa, canal.bloqueios), "forcadas": forcadas}
            return {"proxima": seguinte(etapa), "forcadas": forcadas}
        return executar

    def no_revisao(self, st: EstadoModulo) -> dict:
        p = st["pedido"]
        destino, remetente = p["destino"], p["remetente"]
        anterior = self.artefatos[ARTEFATOS[destino][0]]
        revisoes = st.get("revisoes", 0) + 1
        canal = self.canal(destino)
        mensagem = self.mensagem(destino) + templates.msg_revisao(
            ferramentas.PAPEIS[remetente], p["problemas"], anterior)
        try:
            self.etapa_estruturada(destino, mensagem, self.validador(destino), canal,
                                   rotulo=f"{destino}_revisao")
        except PipelineError:
            # o artefato anterior continua valendo
            self.pendente(remetente, destino, p["problemas"],
                          "a revisão não passou na validação")
            return {"proxima": seguinte(remetente), "pedido": None, "revisoes": revisoes}
        self.amb.enviar(destino, remetente, "resolution", self.nome,
                        "Revised the artifact to address: " + "; ".join(p["problemas"]))
        # roda de novo tudo entre o dono do artefato e quem bloqueou
        i, j = ETAPAS.index(destino), ETAPAS.index(remetente)
        forcadas = sorted(set(st.get("forcadas", [])) | set(ETAPAS[i + 1:j + 1]),
                          key=ETAPAS.index)
        estado = {"proxima": seguinte(destino), "forcadas": forcadas, "pedido": None,
                  "revisoes": revisoes}
        if canal and canal.bloqueios:  # quem revisou pode estar bloqueado mais acima
            estado.update(self.escalonar({**st, "revisoes": revisoes}, destino,
                                         canal.bloqueios))
        return estado

    def no_planejador(self, st: EstadoModulo) -> dict:
        forcada = "planejador" in st.get("forcadas", [])
        forcadas = [x for x in st.get("forcadas", []) if x != "planejador"]
        # Base compartilhada (esqueleto) — só quando há implementação a fazer
        if any(self.roda(e) for e in ETAPAS[4:]) and self.plano_base is None:
            arq = self.artefatos.get("arquitetura")
            self.plano_base = implementacao.garantir_base(
                self.contexto, [self.nome], self.amb.base, self.codigo, self.agentes,
                lado_final(self.ate), self.max_tentativas,
                arquiteturas={self.nome: arq} if arq else None, ambiente=self.amb)
        if self.roda("planejador") or forcada:
            print(f"[5/{ETAPAS.index(self.ate) + 1}] planejador — {self.nome}")
            canal = self.canal("planejador")
            a = self.artefatos
            plano, erros = implementacao.planejar_modulo(
                self.nome, self.contexto, a["especificacao"], a["modelo_dados"],
                a["arquitetura"], self.plano_base, self.numero_migracao, self.outros,
                self.agentes["planejador"], self.metricas, self.max_tentativas,
                leitura=self.amb.leitura(), canal=canal)
            if erros:
                self.falhar("planejador", erros, plano)
            self.amb.publicar(self.nome, "planejador", plano)
            a["plano"] = plano
            if canal and canal.bloqueios:
                return {**self.escalonar(st, "planejador", canal.bloqueios),
                        "forcadas": forcadas}
        return {"proxima": "implementacao", "forcadas": forcadas}

    def no_implementacao(self, st: EstadoModulo) -> dict:
        a, nome = self.artefatos, self.nome
        if self.roda("implementador_backend"):
            print(f"[6/{ETAPAS.index(self.ate) + 1}] implementador_backend — {nome}")
            manifesto = implementacao.implementar_backend(
                nome, self.contexto, a["especificacao"], a["modelo_dados"],
                a["arquitetura"], a["plano"], self.plano_base, self.numero_migracao,
                self.codigo, self.pasta, self.agentes["implementador_backend"],
                self.metricas, self.max_tentativas, leitura=self.amb.leitura(),
                canal=self.canal("implementador_backend"))
            (self.pasta / "6_backend.json").write_text(json.dumps(manifesto, indent=2))
            self.amb.registrar("implementador_backend", "codigo", nome, _caminhos(manifesto))
            a["arquivos_backend"] = manifesto

        if self.roda("implementador_frontend"):
            plano = a["plano"]
            if layout.tem_frontend(self.contexto) and plano.frontend_files:
                print(f"[7/{ETAPAS.index(self.ate) + 1}] implementador_frontend — {nome}")
                manifesto = implementacao.implementar_frontend(
                    nome, self.contexto, a["especificacao"], a["arquitetura"], plano,
                    self.plano_base, self.endpoints_externos or set(), self.codigo,
                    self.pasta, self.agentes["implementador_frontend"], self.metricas,
                    self.max_tentativas, leitura=self.amb.leitura(),
                    canal=self.canal("implementador_frontend"))
                (self.pasta / "7_frontend.json").write_text(json.dumps(manifesto, indent=2))
                self.amb.registrar("implementador_frontend", "codigo", nome,
                                   _caminhos(manifesto))
                a["arquivos_frontend"] = manifesto
            else:
                print(f"[7/7] implementador_frontend — sem frontend para {nome}, pulado")
        return {"proxima": END}

    # --------------------------------------------------------- execução
    def rodar(self) -> dict:
        nos = {e: self.no_especificacao(e) for e in ETAPAS_ESPECIFICACAO}
        nos.update(planejador=self.no_planejador, implementacao=self.no_implementacao,
                   revisao=self.no_revisao)
        montar_grafo(nos).invoke(
            {"forcadas": [], "revisoes": 0, "pedido": None},
            config={"recursion_limit": 20 + 10 * (self.max_revisoes + 1)})

        (self.pasta / "relatorio.md").write_text(
            renderizar_relatorio(self.nome, self.artefatos), encoding="utf-8")
        (self.pasta / "metricas.json").write_text(json.dumps(self.metricas, indent=2))
        artefatos = dict(self.artefatos)
        artefatos["pasta"] = str(self.pasta)
        artefatos["codigo"] = str(self.codigo)
        artefatos["metricas"] = self.metricas
        artefatos["pendencias"] = self.pendencias
        return artefatos
