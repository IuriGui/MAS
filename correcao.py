"""Etapa Build: compila o projeto e os agentes corrigem os erros que são deles.

Laço do orquestrador (até MAX_RODADAS_BUILD rodadas):
  1. compila o projeto inteiro (build.compilar). Erros de módulos que ainda não
     foram implementados ficam de fora;
  2. agrupa os erros por lado e dono do arquivo (módulo ou base) e abre, para
     cada dono, uma sessão de correção do implementador daquele lado
     (agents/corretor.py). Donos com erro que bloqueia o compilador vão primeiro;
  3. para quando compila, quando a rodada não melhora (e volta para a melhor
     versão) ou quando acabam as rodadas. O que sobra vira pendência.

Sessão de correção: o agente entrega edições por trecho (BuildFix) nos próprios
arquivos. A entrega é compilada na hora, sobre uma cópia, e é reprovada se uma
edição não se aplica, se cria erro em arquivo de outro dono ou se sobra erro nos
arquivos dele sem pedido de mudança. Assim o agente corrige dentro do próprio
loop, vendo o resultado do compilador.

Comunicação: quando a causa está num arquivo de outro dono (dependência no
pom.xml da base, método que outro módulo não oferece), o agente chama
request_change. O dono, numa sessão em modo pedido, altera o arquivo ou recusa
explicando o que usar, e a resposta volta na hora como resultado da tool.
Pedidos, respostas e falhas de build ficam em mensagens.jsonl.

Cada arquivo alterado tem a versão anterior guardada em
<modulo>/build/rodada_N/<lado>/<caminho>; o resumo vai para <modulo>/8_build.json.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import build
from agents import ferramentas
from ambiente import Ambiente
from execucao import PipelineError, executar_agente
from layout import slugify
from schemas import BuildFix, CodeEdit, ImplementationPlan, PlannedFile, ProjectContext
from system_prompts import templates

_PREFIXO_LINHA = re.compile(r"^\s*\d+\| ", re.M)


# ------------------------------------------------------------- edições
def aplicar_edicoes(ler: Callable[[str], Optional[str]], edicoes: List[CodeEdit],
                    permitido: Callable[[str], bool]) -> Tuple[Dict[str, str], List[str]]:
    """Aplica as edições em ordem sobre o conteúdo atual. Devolve o conteúdo novo
    de cada arquivo alterado e os erros (em inglês, para o agente)."""
    conteudos: Dict[str, str] = {}
    erros = []
    for i, e in enumerate(edicoes, 1):
        caminho = e.path.strip()
        if not permitido(caminho):
            erros.append(f"Edit {i}: '{caminho}' is not one of your files. To change it, "
                         "call request_change to its owner.")
            continue
        atual = conteudos[caminho] if caminho in conteudos else ler(caminho)
        if atual is None:
            erros.append(f"Edit {i}: '{caminho}' does not exist.")
            continue
        if not e.old_text:
            erros.append(f"Edit {i}: old_text is empty.")
            continue
        n = atual.count(e.old_text)
        if n == 0:
            dica = (" Do not copy the line-number prefixes ('  12| ')."
                    if _PREFIXO_LINHA.search(e.old_text) else "")
            erros.append(f"Edit {i}: old_text was not found in '{caminho}'.{dica} Copy it "
                         "exactly from the current content of the file.")
        elif n > 1:
            erros.append(f"Edit {i}: old_text occurs {n} times in '{caminho}'. Include more "
                         "surrounding lines so that it occurs exactly once.")
        else:
            conteudos[caminho] = atual.replace(e.old_text, e.new_text, 1)
    return conteudos, erros


def _ocorrencias(erros: List[build.Erro]) -> Counter:
    """Erros sem a posição: editar um trecho desloca as linhas dos seguintes."""
    return Counter((e.arquivo, e.mensagem) for e in erros)


def placar(resultados: List[build.Resultado]) -> Tuple[int, int]:
    """(erros bloqueantes, erros) — menor é melhor. Corrigir um erro bloqueante
    pode revelar outros; por isso ele pesa mais que a contagem total."""
    erros = [e for r in resultados for e in r.erros]
    return sum(e.bloqueante for e in erros), len(erros)


@dataclass
class Avaliacao:
    conteudos: Dict[str, str] = field(default_factory=dict)
    erros: List[str] = field(default_factory=list)
    aplicavel: bool = True   # edições se aplicam e não criam erro nos outros


# ------------------------------------------------------------- etapa
class EtapaBuild:
    def __init__(self, *, nome: str, amb: Ambiente, contexto: ProjectContext, agentes: dict,
                 metricas: dict, max_tentativas: int, plano: Optional[ImplementationPlan],
                 plano_base: Optional[ImplementationPlan], pasta: Path, max_rodadas: int,
                 max_pedidos: int, pendente: Callable[[str, str, List[str], str], None],
                 compilar: Optional[Callable[..., List[build.Resultado]]] = None):
        self.nome, self.amb, self.contexto = nome, amb, contexto
        self.agentes, self.metricas, self.max_tentativas = agentes, metricas, max_tentativas
        self.planos = {"base": plano_base, nome: plano}
        self.pasta, self.max_rodadas, self.max_pedidos = pasta, max_rodadas, max_pedidos
        self.pendente, self._compilar = pendente, compilar or build.compilar
        self.modulos = list(contexto.build_order)
        if nome not in self.modulos:
            self.modulos.append(nome)
        self.implementados = {"base", nome} | {
            m for m in self.modulos if (amb.raiz / slugify(m) / "6_backend.json").is_file()}
        self.rodada = 0
        self.diario: List[Tuple[int, str, str, str]] = []  # (rodada, lado, caminho, anterior)
        self.atual: Dict[str, build.Resultado] = {}        # build do disco, por lado
        self.historico: Dict[Tuple[str, str], List[str]] = {}
        self.resumo = {"ok": False, "parada": "", "rodadas": [], "sessoes": 0,
                       "pedidos": 0, "atendidos": 0, "recusados": 0, "revertido_para": None}

    # --------------------------------------------------------- disco e compilação
    def dono(self, caminho: str) -> str:
        return build.dono(caminho, self.modulos)

    def ler(self, lado: str, caminho: str) -> Optional[str]:
        return self.amb.ler(lado, caminho)

    def _relevante(self, e: build.Erro) -> bool:
        """Fora: erros de módulos ainda não implementados (ex.: a base do frontend
        importa as rotas de todos os módulos)."""
        if self.dono(e.arquivo) not in self.implementados:
            return False
        texto = e.mensagem.replace("\\", "/")
        return not any(f"modules/{slugify(m)}/" in texto or f"/{slugify(m)}/routes" in texto
                       for m in self.modulos if m not in self.implementados)

    def compilar(self, lados: Tuple[str, ...] = ("backend", "frontend"),
                 alteracoes: Optional[Dict[str, Dict[str, str]]] = None
                 ) -> Dict[str, build.Resultado]:
        resultados = {}
        for r in self._compilar(self.amb.codigo, lados, alteracoes=alteracoes):
            if not r.falha_de_ambiente:
                r.erros = [e for e in r.erros if self._relevante(e)]
                r.ok = r.ok or not r.erros
            resultados[r.lado] = r
        return resultados

    def build_atual(self, lado: str) -> build.Resultado:
        """Build do disco, refeito só quando algum arquivo do lado mudou."""
        if lado not in self.atual:
            self.atual.update(self.compilar((lado,)))
        return self.atual[lado]

    def gravar(self, lado: str, dono: str, conteudos: Dict[str, str], autor: str) -> None:
        raiz = self.amb.raiz_codigo(lado)
        copia = self.pasta / "build" / f"rodada_{self.rodada}" / lado
        for caminho, conteudo in conteudos.items():
            destino = raiz / caminho
            anterior = destino.read_text(encoding="utf-8")
            self.diario.append((self.rodada, lado, caminho, anterior))
            guardado = copia / caminho
            if not guardado.exists():
                guardado.parent.mkdir(parents=True, exist_ok=True)
                guardado.write_text(anterior, encoding="utf-8")
            destino.write_text(conteudo, encoding="utf-8")
        self.amb.registrar(autor, "codigo", dono, list(conteudos))
        self.atual.pop(lado, None)

    def reverter_ate(self, rodada: int) -> None:
        """Volta os arquivos ao estado do início de `rodada`."""
        while self.diario and self.diario[-1][0] >= rodada:
            _, lado, caminho, anterior = self.diario.pop()
            (self.amb.raiz_codigo(lado) / caminho).write_text(anterior, encoding="utf-8")
            self.atual.pop(lado, None)

    # --------------------------------------------------------- avaliação de uma entrega
    def avaliar(self, lado: str, dono: str, fix: BuildFix, pediu: bool = False,
                tolerar: Optional[str] = None) -> Avaliacao:
        """Aplica as edições numa cópia, compila e compara com o build do disco.

        - `pediu`: o agente pediu mudança a outro dono nesta sessão; erros que
          sobram nos arquivos dele não reprovam a entrega.
        - `tolerar`: no modo pedido, o dono de quem pediu (os arquivos dele podem
          ganhar erros: ele vai se adaptar à mudança).
        """
        conteudos, erros = aplicar_edicoes(
            lambda c: self.ler(lado, c), fix.edits, lambda c: self.dono(c) == dono)
        if erros:
            return Avaliacao(conteudos, erros, aplicavel=False)
        antes = self.build_atual(lado)
        if not conteudos:
            depois = antes
        else:
            depois = self.compilar((lado,), {lado: conteudos})[lado]
        if depois.falha_de_ambiente:
            return Avaliacao(conteudos)  # não dá para julgar; o laço decide

        # erros novos em arquivos de outros donos (ou nos próprios, no modo pedido)
        revelados = (sum(e.bloqueante for e in depois.erros)
                     < sum(e.bloqueante for e in antes.erros))
        vigiado = (lambda d: d != dono) if tolerar is None else (lambda d: d != tolerar)
        novos = (_ocorrencias([e for e in depois.erros if vigiado(self.dono(e.arquivo))])
                 - _ocorrencias(antes.erros))
        if novos and not revelados:
            arquivos = sorted({a for a, _ in novos})
            return Avaliacao(conteudos, [
                "Your edits create new build errors in files you do not own; keep the "
                "signatures other code uses (add instead of changing):\n"
                + build.para_agente([build.Resultado(lado, False, depois.passos, erros=[
                    e for e in depois.erros if e.arquivo in arquivos])])], aplicavel=False)

        if tolerar is not None:
            return Avaliacao(conteudos)
        meus = [e for e in depois.erros if self.dono(e.arquivo) == dono]
        if meus and not pediu:
            return Avaliacao(conteudos, [
                ("No edits were submitted. " if not conteudos else "")
                + "After applying your edits, these build errors remain in your files. Fix "
                "them, or call request_change if the cause is in a file you do not own:\n"
                + build.para_agente([build.Resultado(lado, False, depois.passos, erros=meus)])])
        return Avaliacao(conteudos)

    # --------------------------------------------------------- agentes
    def agente(self, lado: str, modo: str):
        chave = f"build:{lado}" + (":pedido" if modo == "pedido" else "")
        if chave not in self.agentes:
            from agents import corretor
            self.agentes[chave] = corretor.get_agente(lado, self.contexto, modo)
        return self.agentes[chave]

    def plano_de(self, dono: str, lado: str) -> Dict[str, PlannedFile]:
        if dono not in self.planos:
            self.planos[dono] = self.amb.artefato(dono, "plano")
        plano = self.planos[dono]
        return {f.path: f for f in getattr(plano, f"{lado}_files")} if plano else {}

    @staticmethod
    def quem(dono: str) -> str:
        return "the shared base" if dono == "base" else f"module {dono}"

    # --------------------------------------------------------- sessões
    def corrigir(self, lado: str, dono: str, resultado: build.Resultado) -> None:
        meus = [e for e in resultado.erros if self.dono(e.arquivo) == dono]
        arquivos = list(dict.fromkeys(e.arquivo for e in meus if e.arquivo))
        conteudos = {c: t for c in arquivos if (t := self.ler(lado, c)) is not None}
        etapa = f"implementador_{lado}"
        relatorio = build.para_agente([resultado], arquivos={e.arquivo for e in meus})
        print(f"  [build {lado}] {dono}: {len(meus)} erro(s) em {len(arquivos)} arquivo(s)")
        self.amb.enviar("orquestrador", etapa, "build_failure", dono, relatorio)
        self.resumo["sessoes"] += 1

        canal = ferramentas.Canal(
            etapa=etapa,
            pedir_mudanca=lambda caminho, problema: self.pedir(lado, dono, caminho, problema))
        mensagem = templates.msg_correcao(dono, lado, relatorio, conteudos,
                                          self.plano_de(dono, lado),
                                          self.historico.get((lado, dono), []))
        try:
            fix, erros = executar_agente(
                f"build_{lado}", self.agente(lado, "correcao"), mensagem,
                lambda f: self.avaliar(lado, dono, f, pediu=canal.pedidos > 0).erros,
                self.metricas, self.max_tentativas, modulo=dono,
                leitura=self.amb.leitura(), lado=lado, canal=canal)
        except PipelineError as e:
            print(f"  [build {lado}] {dono}: sem resposta ({e})")
            return
        if erros:
            # a última entrega reprovada ainda vale se não quebra nada: é progresso
            av = self.avaliar(lado, dono, fix, pediu=True)
            if not av.aplicavel or not av.conteudos:
                print(f"  [build {lado}] {dono}: correção descartada: {erros[0][:120]}")
                self.historico.setdefault((lado, dono), []).append(
                    f"Round {self.rodada}: your fix was discarded: {erros[0][:300]}")
                return
            conteudos_novos = av.conteudos
        else:
            conteudos_novos = aplicar_edicoes(lambda c: self.ler(lado, c), fix.edits,
                                              lambda c: self.dono(c) == dono)[0]
        if conteudos_novos:
            self.gravar(lado, dono, conteudos_novos, f"build_{lado}")
            self.historico.setdefault((lado, dono), []).append(
                f"Round {self.rodada}: you edited {', '.join(conteudos_novos)}.")

    def pedir(self, lado: str, remetente: str, caminho: str, problema: str) -> str:
        """request_change: leva o pedido ao dono do arquivo e devolve a resposta."""
        caminho = caminho.strip()
        dono = self.dono(caminho)
        if dono == remetente:
            return f"'{caminho}' is one of your own files: edit it yourself."
        conteudo = self.ler(lado, caminho)
        if conteudo is None:
            return f"'{caminho}' does not exist on the {lado} side. Use list_files."
        if self.resumo["pedidos"] >= self.max_pedidos:
            return ("Change request budget exhausted for this build stage: fix what you can "
                    "in your own files and submit.")
        self.resumo["pedidos"] += 1
        etapa = f"implementador_{lado}"
        papel = f"{ferramentas.PAPEIS[etapa]} of {self.quem(remetente)}"
        print(f"  [build {lado}] {remetente} pede ao dono ({dono}) mudança em {caminho}: "
              f"{problema[:100]}")
        q = self.amb.enviar(etapa, etapa, "change_request", remetente,
                            f"{caminho}: {problema}", modulo_destino=dono)
        mensagem = templates.msg_pedido_mudanca(dono, lado, papel, caminho, problema,
                                                conteudo, self.plano_de(dono, lado))
        try:
            fix, erros = executar_agente(
                f"build_{lado}_pedido", self.agente(lado, "pedido"), mensagem,
                lambda f: self.avaliar(lado, dono, f, tolerar=remetente).erros,
                self.metricas, self.max_tentativas, modulo=dono,
                leitura=self.amb.leitura(), lado=lado)
        except PipelineError:
            fix, erros = None, ["sem resposta"]

        if fix is None or erros:
            tipo = "declined"
            resposta = (f"The owner ({self.quem(dono)}) could not make a valid change to "
                        f"{caminho}. Fix what you can in your own files.")
        elif fix.edits:
            conteudos = aplicar_edicoes(lambda c: self.ler(lado, c), fix.edits,
                                        lambda c: self.dono(c) == dono)[0]
            self.gravar(lado, dono, conteudos, f"build_{lado}_pedido")
            tipo = "resolution"
            resposta = (f"The owner ({self.quem(dono)}) changed {', '.join(conteudos)}: "
                        f"{fix.reply or 'done'}")
        else:
            tipo = "declined"
            resposta = f"The owner ({self.quem(dono)}) declined: {fix.reply or 'no reason given'}"
        self.resumo["atendidos" if tipo == "resolution" else "recusados"] += 1
        self.amb.enviar(etapa, etapa, tipo, dono, resposta, em_resposta_a=q,
                        modulo_destino=remetente)
        self.historico.setdefault((lado, remetente), []).append(
            f"Round {self.rodada}: you requested a change to {caminho}. {resposta}")
        print(f"  [build {lado}] {dono} → {remetente}: {tipo}")
        return resposta

    # --------------------------------------------------------- laço
    def _registrar_rodada(self, resultados: Dict[str, build.Resultado]) -> None:
        por_dono = Counter(self.dono(e.arquivo) for r in resultados.values() for e in r.erros)
        self.resumo["rodadas"].append({
            "rodada": self.rodada,
            **{lado: {"ok": r.ok, "erros": len(r.erros),
                      "bloqueantes": sum(e.bloqueante for e in r.erros),
                      "falha_de_ambiente": r.falha_de_ambiente, "segundos": r.segundos}
               for lado, r in resultados.items()},
            "por_dono": dict(sorted(por_dono.items())),
        })

    def _ordem(self, resultados: Dict[str, build.Resultado]) -> List[Tuple[str, str]]:
        """(lado, dono) com erro: quem bloqueia o compilador primeiro, depois o
        módulo atual, a base e os demais."""
        grupos: Dict[Tuple[str, str], bool] = {}
        for lado, r in resultados.items():
            if r.falha_de_ambiente:
                continue
            for e in r.erros:
                chave = (lado, self.dono(e.arquivo))
                grupos[chave] = grupos.get(chave, False) or e.bloqueante
        return sorted(grupos, key=lambda g: (not grupos[g], g[1] != self.nome,
                                             g[1] != "base", g))

    def rodar(self) -> dict:
        melhor: Optional[Tuple[Tuple[int, int], int]] = None  # (placar, rodada)
        for self.rodada in range(1, self.max_rodadas + 2):
            self.atual = self.compilar()
            resultados = dict(self.atual)
            self._registrar_rodada(resultados)
            pontos = placar(list(resultados.values()))
            print(f"  [build] rodada {self.rodada}: "
                  + ", ".join(f"{l} {'ok' if r.ok else f'{len(r.erros)} erro(s)'}"
                              for l, r in resultados.items()))
            if not resultados:
                self.resumo["parada"] = "sem_codigo"
                break
            if all(r.ok for r in resultados.values()):
                self.resumo.update(ok=True, parada="ok")
                break
            if all(r.ok or r.falha_de_ambiente for r in resultados.values()):
                self.resumo["parada"] = "ambiente"
                break
            if melhor is not None and pontos >= melhor[0]:
                if pontos > melhor[0]:
                    self.reverter_ate(melhor[1])
                    self.atual = {}
                    self.resumo["revertido_para"] = melhor[1]
                    print(f"  [build] rodada {self.rodada} piorou: volta à rodada {melhor[1]}")
                self.resumo["parada"] = "sem_progresso"
                break
            melhor = (pontos, self.rodada)
            if self.rodada > self.max_rodadas:
                self.resumo["parada"] = "limite"
                break
            for lado, dono in self._ordem(resultados):
                atual = self.build_atual(lado)
                if any(self.dono(e.arquivo) == dono for e in atual.erros):
                    self.corrigir(lado, dono, atual)

        if not self.resumo["ok"]:
            final = self.atual or self.compilar()
            for lado, r in final.items():
                if r.falha_de_ambiente:
                    self.pendente("build", f"implementador_{lado}",
                                  [e.mensagem for e in r.erros], "build não rodou na máquina")
                    continue
                grupos = Counter(self.dono(e.arquivo) for e in r.erros)
                for dono, n in sorted(grupos.items()):
                    arquivos = sorted({e.arquivo or "(projeto)" for e in r.erros
                                       if self.dono(e.arquivo) == dono})
                    onde = "na base" if dono == "base" else f"no módulo {dono}"
                    self.pendente("build", f"implementador_{lado}",
                                  [f"{n} erro(s) de build {onde}: " + ", ".join(arquivos)],
                                  f"build não passou ({self.resumo['parada']})")
        return self.resumo
