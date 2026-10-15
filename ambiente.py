"""Ambiente compartilhado do projeto (F1): o "quadro" dos agentes.

Guarda o que já foi aceito no projeto:
  - o PROJECT CONTEXT;
  - os artefatos de cada módulo (análise, especificação, modelo, arquitetura, plano);
  - os contratos publicados (serviços públicos, eventos e endpoints de cada módulo);
  - o código gerado (backend e frontend).

Regras:
  - Só o orquestrador escreve (`publicar_*`, `registrar`), e só saídas aprovadas
    na validação. Cada escrita vai para o registro (`ambiente.jsonl`): quem
    publicou, o quê e quando. É a comunicação indireta da IBM: um agente
    modifica o ambiente e os outros percebem a modificação.
  - Os agentes só leem, pela fachada `Leitura`, que é a base das tools da F2.
  - O disco é a única fonte da verdade: nada fica guardado só em memória.
  - Mensagens diretas entre agentes (F3: consultas, bloqueios, revisões) também
    passam pelo orquestrador e ficam em `mensagens.jsonl`.

"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from assinaturas import assinaturas
from layout import slugify
from schemas import (AgentMessage, FinalSpecification, ImplementationPlan,
                     ModuleAnalysis, ModuleArchitecture, ModuleDataModel,
                     ProjectContext)

LADOS = ("backend", "frontend")
_IGNORAR = {"node_modules", "target", "build", "dist", ".git", ".gradle", ".idea"}

# etapa -> (chave do artefato, arquivo, schema)
ARTEFATOS = {
    "requisitos": ("analise", "1_analise.json", ModuleAnalysis),
    "esclarecedor": ("especificacao", "2_especificacao.json", FinalSpecification),
    "modelador_dados": ("modelo_dados", "3_modelo_dados.json", ModuleDataModel),
    "arquiteto_software": ("arquitetura", "4_arquitetura.json", ModuleArchitecture),
    "planejador": ("plano", "5_plano.json", ImplementationPlan),
}
POR_CHAVE = {chave: (etapa, arquivo, schema)
             for etapa, (chave, arquivo, schema) in ARTEFATOS.items()}


def caminhos_em_disco(raiz: Path) -> Set[str]:
    if not raiz.exists():
        return set()
    return {p.relative_to(raiz).as_posix() for p in raiz.rglob("*")
            if p.is_file() and not _IGNORAR & set(p.relative_to(raiz).parts)}


class Ambiente:
    def __init__(self, raiz: Path, contexto: Optional[ProjectContext] = None,
                 codigo: Optional[Path] = None, base: Optional[Path] = None,
                 registro: Optional[Path] = None, mensagens: Optional[Path] = None):
        self.raiz = Path(raiz)
        self.contexto = contexto
        self.codigo = Path(codigo) if codigo else self.raiz / "codigo"
        self.base = Path(base) if base else self.raiz / "base"
        self.arquivo_registro = Path(registro) if registro else self.raiz / "ambiente.jsonl"
        self.arquivo_mensagens = (Path(mensagens) if mensagens
                                  else self.arquivo_registro.parent / "mensagens.jsonl")

    # --------------------------------------------------------- caminhos
    def pasta_modulo(self, modulo: str) -> Path:
        pasta = self.raiz / slugify(modulo)
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta

    def raiz_codigo(self, lado: str) -> Path:
        if lado not in LADOS:
            raise ValueError(f"lado deve ser um de {LADOS}")
        return self.codigo / lado

    # --------------------------------------------------------- escrita (orquestrador)
    def registrar(self, autor: str, tipo: str, modulo: str, alvos: Iterable[str]) -> None:
        """Anota no registro uma modificação do ambiente."""
        self.arquivo_registro.parent.mkdir(parents=True, exist_ok=True)
        linha = {"quando": datetime.now().isoformat(timespec="seconds"),
                 "autor": autor, "tipo": tipo, "modulo": modulo, "alvos": list(alvos)}
        with self.arquivo_registro.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")

    def enviar(self, remetente: str, destinatario: str, tipo: str, modulo: str,
               conteudo: str, em_resposta_a: Optional[str] = None) -> str:
        """Registra uma mensagem entre agentes e devolve o id dela."""
        msg = AgentMessage(
            id=f"M{len(self.mensagens()) + 1:03d}", sender=remetente,
            recipient=destinatario, kind=tipo, module=modulo, content=conteudo,
            in_reply_to=em_resposta_a,
            sent_at=datetime.now().isoformat(timespec="seconds"))
        self.arquivo_mensagens.parent.mkdir(parents=True, exist_ok=True)
        with self.arquivo_mensagens.open("a", encoding="utf-8") as f:
            f.write(msg.model_dump_json() + "\n")
        return msg.id

    def mensagens(self, modulo: Optional[str] = None) -> List[AgentMessage]:
        if not self.arquivo_mensagens.is_file():
            return []
        todas = [AgentMessage.model_validate_json(l) for l in
                 self.arquivo_mensagens.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [m for m in todas if modulo is None or m.module == modulo]

    def publicar_contexto(self, contexto: ProjectContext,
                          autor: str = "arquiteto_chefe") -> None:
        self.raiz.mkdir(parents=True, exist_ok=True)
        (self.raiz / "0_contexto.json").write_text(
            contexto.model_dump_json(indent=2), encoding="utf-8")
        self.contexto = contexto
        self.registrar(autor, "contexto", "", ["0_contexto.json"])

    def publicar(self, modulo: str, etapa: str, obj, autor: Optional[str] = None) -> None:
        """Grava o artefato aprovado de `etapa` do módulo."""
        _, arquivo, _ = ARTEFATOS[etapa]
        destino = self.pasta_modulo(modulo) / arquivo
        destino.write_text(obj.model_dump_json(indent=2), encoding="utf-8")
        self.registrar(autor or etapa, "artefato", modulo,
                       [destino.relative_to(self.raiz).as_posix()])

    # --------------------------------------------------------- leitura
    def artefato(self, modulo: str, chave: str):
        """Artefato publicado do módulo, ou None se ainda não existe."""
        _, arquivo, schema = POR_CHAVE[chave]
        caminho = self.raiz / slugify(modulo) / arquivo
        if not caminho.is_file():
            return None
        return schema.model_validate_json(caminho.read_text(encoding="utf-8"))

    def caminho_artefato(self, modulo: str, chave: str) -> Path:
        return self.raiz / slugify(modulo) / POR_CHAVE[chave][1]

    def modulos(self) -> List[str]:
        return list(self.contexto.build_order) if self.contexto else []

    def arquiteturas(self) -> Dict[str, ModuleArchitecture]:
        """Arquiteturas publicadas, na ordem de construção."""
        achadas = {}
        for nome in self.modulos():
            arq = self.artefato(nome, "arquitetura")
            if arq:
                achadas[nome] = arq
        return achadas

    def endpoints_de_outros(self, modulo: str) -> Set[str]:
        return {e.path for n, a in self.arquiteturas().items()
                if slugify(n) != slugify(modulo) for e in a.endpoints}

    def contratos(self, exceto: Optional[str] = None) -> List[dict]:
        """O que cada módulo já publicou para os outros consumirem."""
        saida = []
        for nome, a in self.arquiteturas().items():
            if exceto and slugify(nome) == slugify(exceto):
                continue
            saida.append({
                "module": nome,
                "public_services": [s.model_dump() for s in a.public_services],
                "domain_events": [e.model_dump() for e in a.domain_events],
                "endpoints": [f"{e.method} {e.path}" for e in a.endpoints],
            })
        return saida

    def arquivos(self, lado: str) -> Set[str]:
        return caminhos_em_disco(self.raiz_codigo(lado))

    def ler(self, lado: str, caminho: str) -> Optional[str]:
        """Conteúdo de um arquivo de código; None se não existe ou sai da raiz."""
        raiz = self.raiz_codigo(lado).resolve()
        alvo = (raiz / caminho).resolve()
        if not alvo.is_relative_to(raiz) or not alvo.is_file():
            return None
        return alvo.read_text(encoding="utf-8")

    def registro(self) -> List[dict]:
        if not self.arquivo_registro.is_file():
            return []
        return [json.loads(l) for l in
                self.arquivo_registro.read_text(encoding="utf-8").splitlines() if l.strip()]

    def leitura(self) -> "Leitura":
        return Leitura(self)


class Leitura:
    """Visão só de leitura do ambiente, para os agentes.

    Devolve texto, porque é isso que uma tool entrega ao modelo. As mensagens
    são em inglês, como os prompts.
    """

    def __init__(self, ambiente: Ambiente):
        self._amb = ambiente

    def contexto(self) -> str:
        c = self._amb.contexto
        return c.model_dump_json(indent=2) if c else "No PROJECT CONTEXT published yet."

    def artefato(self, modulo: str, chave: str) -> str:
        if chave not in POR_CHAVE:
            return f"Unknown artifact '{chave}'. Use one of: {sorted(POR_CHAVE)}."
        obj = self._amb.artefato(modulo, chave)
        if obj is None:
            return f"Module '{modulo}' has not published '{chave}' yet."
        return obj.model_dump_json(indent=2)

    def contratos(self, exceto: Optional[str] = None) -> str:
        return json.dumps(self._amb.contratos(exceto), indent=2, ensure_ascii=False)

    def listar_arquivos(self, lado: str, prefixo: str = "") -> str:
        if lado not in LADOS:
            return f"Unknown side '{lado}'. Use one of: {list(LADOS)}."
        caminhos = sorted(c for c in self._amb.arquivos(lado) if c.startswith(prefixo))
        return "\n".join(caminhos) if caminhos else f"No {lado} files under '{prefixo}'."

    def ler_arquivo(self, lado: str, caminho: str) -> str:
        if lado not in LADOS:
            return f"Unknown side '{lado}'. Use one of: {list(LADOS)}."
        texto = self._amb.ler(lado, caminho)
        return texto if texto is not None else f"File '{caminho}' does not exist ({lado})."

    def assinaturas(self, lado: str, caminho: str) -> str:
        if lado not in LADOS:
            return f"Unknown side '{lado}'. Use one of: {list(LADOS)}."
        texto = self._amb.ler(lado, caminho)
        if texto is None:
            return f"File '{caminho}' does not exist ({lado})."
        return assinaturas(caminho, texto) or f"No public signatures found in '{caminho}'."
