"""Coleta do lote que um Implementador gravou no sandbox (modo SANDBOX).

O deep agent escreve direto no workspace do container (montado do host). Depois
de cada tentativa, ColetaSandbox transforma o disco em GeneratedFiles para a
validação de sempre, repara caminhos trocados e desfaz o que mudou fora do lote.
"""
from pathlib import Path
from typing import Dict, List, Set

from mas.codigo.arquivos import caminhos_em_disco, reparar_caminhos
from mas.schemas import GeneratedFile, GeneratedFiles, PlannedFile

# Gerados pelas ferramentas de build: não são descartados quando aparecem
_SUBPRODUTOS = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"}
_SUFIXOS_SUBPRODUTO = (".tsbuildinfo", ".log")


def _foto(raiz: Path) -> Dict[str, bytes]:
    return {c: (raiz / c).read_bytes() for c in caminhos_em_disco(raiz)}


def _subproduto(caminho: str) -> bool:
    nome = Path(caminho).name
    return nome in _SUBPRODUTOS or nome.endswith(_SUFIXOS_SUBPRODUTO)


def _texto_final(resultado: dict) -> str:
    """Texto da última mensagem do agente (o resumo do que ele verificou)."""
    for m in reversed(resultado.get("messages", [])):
        if getattr(m, "type", "") != "ai" or getattr(m, "tool_calls", None):
            continue
        c = m.content
        if isinstance(c, list):
            c = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
        return str(c).strip()
    return ""


class ColetaSandbox:
    """Liga um lote ao disco do sandbox (o agente grava direto no workspace).

    Fotografa o workspace inteiro antes do lote. A cada tentativa (chamada como
    `extrair` de executar_agente):
    - colhe do disco os arquivos pedidos e, como candidatos a caminho trocado,
      os arquivos deste lado criados ou alterados fora do lote;
    - aplica reparar_caminhos e grava os reparados no caminho certo;
    - desfaz tudo o que mudou fora do lote (restaura alterados, apaga criados,
      exceto subprodutos de build), inclusive no outro lado do projeto.
    Assim o disco sempre corresponde ao que foi validado.
    """

    def __init__(self, raiz_sandbox: Path, raiz_lado: Path, lote: List[PlannedFile]):
        self.raiz = Path(raiz_sandbox).resolve()
        rel = Path(raiz_lado).resolve().relative_to(self.raiz).as_posix()
        self.prefixo = "" if rel == "." else rel + "/"
        self.lote = lote
        self.antes = _foto(self.raiz)

    def _no_sandbox(self, caminho_lado: str) -> str:
        return self.prefixo + caminho_lado

    def __call__(self, resultado: dict) -> GeneratedFiles:
        agora = _foto(self.raiz)
        pedidos = {self._no_sandbox(f.path): f.path for f in self.lote}
        arquivos = [GeneratedFile(path=lado, content=agora[s].decode("utf-8", errors="replace"))
                    for s, lado in pedidos.items() if s in agora]
        candidatos = [s for s in agora if s not in pedidos and s.startswith(self.prefixo)
                      and agora[s] != self.antes.get(s) and not _subproduto(s)]
        arquivos += [GeneratedFile(path=s[len(self.prefixo):],
                                   content=agora[s].decode("utf-8", errors="replace"))
                     for s in candidatos]
        resumo = _texto_final(resultado)
        gerados = GeneratedFiles(files=arquivos, notes=[resumo] if resumo else [])
        reparar_caminhos(gerados, self.lote)

        vistos: Set[str] = set()
        no_lote = []
        for f in gerados.files:
            if f.path in vistos:
                continue
            vistos.add(f.path)
            if self._no_sandbox(f.path) in pedidos:
                no_lote.append(f)
        gerados.files = no_lote
        for f in no_lote:   # reparados vão para o caminho certo
            destino = self.raiz / self._no_sandbox(f.path)
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(f.content.encode("utf-8"))
        entregues = {self._no_sandbox(f.path) for f in no_lote}
        for s in pedidos:   # caminho pedido cujo conteúdo era de outro arquivo
            if s not in entregues and s in agora:
                self._voltar(s)

        descartados = self._restaurar(agora, manter=set(pedidos))
        if descartados:
            nota = f"Mudanças fora do lote descartadas: {descartados}"
            gerados.notes.append(nota)
            print(f"  sandbox: {nota}")
        return gerados

    def _restaurar(self, agora: Dict[str, bytes], manter: Set[str]) -> List[str]:
        mudados = []
        for s in sorted(set(agora) | set(self.antes)):
            if s in manter or agora.get(s) == self.antes.get(s):
                continue
            if s not in self.antes and _subproduto(s):
                continue
            mudados.append(s)
            self._voltar(s)
        return mudados

    def _voltar(self, s: str) -> None:
        caminho = self.raiz / s
        if s in self.antes:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_bytes(self.antes[s])
            return
        caminho.unlink(missing_ok=True)
        pasta = caminho.parent
        while pasta != self.raiz and pasta.is_dir() and not any(pasta.iterdir()):
            pasta.rmdir()
            pasta = pasta.parent

    def desfazer(self) -> None:
        """Lote reprovado: o disco volta ao estado de antes do lote."""
        agora = _foto(self.raiz)
        for s in {self._no_sandbox(f.path) for f in self.lote}:
            if agora.get(s) != self.antes.get(s):
                self._voltar(s)
        self._restaurar(_foto(self.raiz), manter=set())
