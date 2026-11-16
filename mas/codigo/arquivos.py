"""Arquivos gerados em disco: listar, gravar com segurança e reparar caminhos.

- caminhos_em_disco ignora pastas de build e dependências (node_modules, target...).
- caminho_seguro recusa caminhos inválidos ou fora da raiz do lado.
- reparar_caminhos corrige, em código, o arquivo que o modelo escreveu com o
  caminho de outro arquivo do lote.
"""
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from mas.codigo import layout
from mas.pipeline.execucao import PipelineError
from mas.schemas import GeneratedFiles, PlannedFile

_IGNORAR = {"node_modules", "target", "build", "dist", ".git", ".gradle", ".idea"}


# ------------------------------------------------------------- disco
def caminhos_em_disco(raiz: Path) -> Set[str]:
    if not raiz.exists():
        return set()
    return {p.relative_to(raiz).as_posix() for p in raiz.rglob("*")
            if p.is_file() and not _IGNORAR & set(p.relative_to(raiz).parts)}


def caminho_seguro(raiz: Path, caminho: str) -> Path:
    motivo = layout.caminho_invalido(caminho)
    destino = (raiz / caminho).resolve()
    if motivo or not destino.is_relative_to(raiz.resolve()):
        raise PipelineError(f"Caminho recusado ao gravar: '{caminho}' ({motivo or 'fora da raiz'}).")
    return destino


def gravar(raiz: Path, gerados: GeneratedFiles) -> List[str]:
    gravados = []
    for f in gerados.files:
        destino = caminho_seguro(raiz, f.path)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(layout.limpar_conteudo(f.content), encoding="utf-8")
        gravados.append(f.path)
    return gravados


# ------------------------------------------------------------- reparo
_TIPO_JVM = re.compile(
    r"^(?:public\s+|internal\s+)?(?:(?:final|abstract|sealed|open|data|static)\s+)*"
    r"(?:class|interface|enum|record|object|@interface)\s+([A-Za-z_]\w*)", re.M)
_PACOTE_JVM = re.compile(r"^\s*package\s+([\w.]+)\s*;?\s*$", re.M)


def _tipo_declarado(conteudo: str) -> Optional[Tuple[str, str]]:
    """(pacote, primeiro tipo de topo) de um arquivo Java/Kotlin."""
    pacote = _PACOTE_JVM.search(conteudo)
    tipo = _TIPO_JVM.search(conteudo)
    return (pacote.group(1), tipo.group(1)) if pacote and tipo else None


def _exporta_tudo(conteudo: str, nomes: List[str]) -> bool:
    """O conteúdo declara num `export` todos os nomes (TS/JS)."""
    return bool(nomes) and all(
        re.search(rf"^\s*export\b[^\n]*\b{re.escape(n)}\b", conteudo, re.M) for n in nomes)


def reparar_caminhos(gerados: GeneratedFiles, pedidos: List[PlannedFile]) -> None:
    """Corrige em código um erro recorrente do modelo: escrever o arquivo X com o
    `path` de outro arquivo do lote (um arquivo, ou vários em cadeia).

    - Java/Kotlin: o caminho é função do pacote e do tipo declarados, então todo
      arquivo que declara P.X vai para o caminho pedido de P.X. Não mexe quando
      dois arquivos declaram o mesmo tipo.
    - Demais (TS/JS...): o arquivo duplicado ou não pedido é reatribuído quando
      exporta todos os `exports` planejados de exatamente um arquivo que falta, e
      não os do seu próprio caminho.
    """
    por_caminho = {f.path: f for f in pedidos}
    por_fqcn = {layout.fqcn(p): p for p in por_caminho if layout.fqcn(p)}

    # Java/Kotlin: reatribuição global pelo tipo declarado
    declarados = {}
    for i, f in enumerate(gerados.files):
        if f.path.endswith((".java", ".kt")):
            d = _tipo_declarado(layout.limpar_conteudo(f.content))
            if d:
                declarados[i] = f"{d[0]}.{d[1]}"
    repetidos = {fq for fq in declarados.values() if list(declarados.values()).count(fq) > 1}
    for i, fq in declarados.items():
        f = gerados.files[i]
        novo = por_fqcn.get(fq)
        if novo and novo != f.path and fq not in repetidos:
            gerados.notes.append(f"Caminho corrigido automaticamente: {f.path} -> {novo}")
            print(f"  reparo: {f.path} -> {novo}")
            f.path = novo

    # Demais: pelos exports planejados
    entregues = [f.path for f in gerados.files]
    faltando = [p for p in por_caminho if p not in entregues]
    for f in gerados.files:
        if not faltando:
            return
        if f.path.endswith((".java", ".kt")):
            continue
        if entregues.count(f.path) > 1 or f.path not in por_caminho:
            conteudo = layout.limpar_conteudo(f.content)
            proprio = por_caminho.get(f.path)
            if proprio and _exporta_tudo(conteudo, proprio.exports):
                continue
            candidatos = [p for p in faltando if _exporta_tudo(conteudo, por_caminho[p].exports)]
            if len(candidatos) == 1:
                novo = candidatos[0]
                faltando.remove(novo)
                entregues[entregues.index(f.path)] = novo
                gerados.notes.append(f"Caminho corrigido automaticamente: {f.path} -> {novo}")
                print(f"  reparo: {f.path} -> {novo}")
                f.path = novo
