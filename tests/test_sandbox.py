"""Implementadores no modo sandbox: o agente grava no disco e o pipeline colhe.

Os testes de coleta usam um agente falso que escreve direto na pasta do código
(como o deep agent faria pelo container). O de integração sobe o Docker de
verdade e é pulado quando não há Docker ou a imagem do sandbox.
"""
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

import pytest

from mas.agentes.fabrica import AgenteSandbox
from mas.config import SANDBOX_IMAGEM
from mas.pipeline.execucao import PipelineError
from mas.pipeline.modulo import carregar_contexto, processar_modulo
from mas.schemas import PlannedFile
from tests.fakes import PKG, agentes, conteudo, pedidos

MAIN = f"src/main/java/{PKG}"


class SandboxFalso:
    def __init__(self, host_dir: Path):
        self.host_dir = Path(host_dir).resolve()

    def caminho_no_container(self, caminho: Path) -> str:
        rel = Path(caminho).resolve().relative_to(self.host_dir).as_posix()
        return "/workspace" if rel == "." else f"/workspace/{rel}"


def sandbox_falso(host_dir: Path,
                  extra: Optional[Callable[[Path, List[PlannedFile], str], None]] = None):
    """Agente que grava no disco os arquivos pedidos; `extra(raiz_lado, lote, msg)`
    faz o que mais o teste quiser (ex.: gravar fora do lote)."""
    sb = SandboxFalso(host_dir)
    chamadas: List[str] = []

    class Grafo:
        def invoke(self, entrada, config=None):
            msg = entrada["messages"][0]["content"]
            chamadas.append(msg)
            raiz_c = re.search(r"Side root: (\S+)", msg).group(1)
            raiz = sb.host_dir / raiz_c.removeprefix("/workspace").lstrip("/")
            lote = pedidos(msg)
            for f in lote:
                destino = raiz / f.path
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_text(conteudo(f), encoding="utf-8")
            if extra:
                extra(raiz, lote, msg)
            return {"messages": []}

    ag = AgenteSandbox(Grafo(), sb, "- PostgreSQL 16: host 'db', porta 5432")
    ag.chamadas = chamadas
    return ag


def _rodar(tmp_path, back=None, front=None, **kw):
    codigo = tmp_path / "usuarios" / "codigo"
    ags = agentes(back=back(codigo) if back else sandbox_falso(codigo),
                  front=front(codigo) if front else sandbox_falso(codigo))
    res = processar_modulo("Usuários", "desc", agentes=ags, saida_dir=str(tmp_path), **kw)
    return res, codigo, ags


def test_sandbox_gera_codigo_pelo_disco(tmp_path):
    res, codigo, ags = _rodar(tmp_path)
    assert (codigo / "backend" / MAIN / "domain/User.java").exists()
    assert (codigo / "frontend" / "src/main.tsx").exists()
    assert res["metricas"]["implementador_backend"]["lotes"] == 2
    msg = ags["implementador_backend"].chamadas[-1]
    assert "Side root: /workspace/backend" in msg
    assert f"/workspace/backend/{MAIN}/api/UserController.java" in msg
    assert "host 'db'" in msg


def test_sandbox_descarta_mudancas_fora_do_lote(tmp_path):
    def bagunca(raiz, lote, msg):
        if "MODE: MODULE" not in msg:
            return
        (raiz / "pom.xml").write_text("<estragado/>")                 # da base
        (raiz / "Rascunho.java").write_text("class Rascunho {}")       # avulso
        (raiz / "package-lock.json").write_text("{}")                  # subproduto
        outro = raiz.parent / "frontend" / "src" / "main.tsx"          # outro lado
        if outro.exists():
            outro.write_text("quebrado")

    _, codigo, _ = _rodar(tmp_path, back=lambda c: sandbox_falso(c, bagunca))
    back = codigo / "backend"
    assert (back / "pom.xml").read_text() == "{}\n"
    assert not (back / "Rascunho.java").exists()
    assert (back / "package-lock.json").exists()
    assert (codigo / "frontend" / "src/main.tsx").read_text() != "quebrado"


def test_sandbox_repara_caminho_trocado(tmp_path):
    """O agente grava User.java no caminho de Role.java e vice-versa."""
    def troca(raiz, lote, msg):
        caminhos = {f.path for f in lote}
        a, b = f"{MAIN}/domain/User.java", f"{MAIN}/domain/Role.java"
        if a in caminhos and b in caminhos:
            ta, tb = (raiz / a).read_text(), (raiz / b).read_text()
            (raiz / a).write_text(tb)
            (raiz / b).write_text(ta)

    res, codigo, ags = _rodar(tmp_path, back=lambda c: sandbox_falso(c, troca))
    user = (codigo / "backend" / MAIN / "domain/User.java").read_text()
    assert "public class User " in user
    # reparado em código: nenhuma tentativa extra
    m = res["metricas"]["implementador_backend"]
    assert m["tentativas"] == m["lotes"]


def test_sandbox_lote_reprovado_volta_o_disco(tmp_path):
    def estraga(raiz, lote, msg):
        if "MODE: MODULE" in msg:
            (raiz / lote[0].path).write_text("class Errada {}")

    with pytest.raises(PipelineError):
        _rodar(tmp_path, back=lambda c: sandbox_falso(c, estraga), max_tentativas=2)
    back = tmp_path / "usuarios" / "codigo" / "backend"
    assert not (back / MAIN / "domain/User.java").exists()
    assert (back / "pom.xml").exists()                     # a base continua


# ----------------------------------------------------------- Docker real
def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", SANDBOX_IMAGEM], capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="Docker ou imagem do sandbox indisponível")
def test_ambiente_docker_rede_com_postgres(tmp_path):
    from mas.agentes.sandbox import AmbienteDocker, servicos_do_contexto

    servicos = [s for s in servicos_do_contexto(carregar_contexto()) if s.alias == "db"]
    with AmbienteDocker(str(tmp_path), servicos, portas=()) as amb:
        assert [s.alias for s in amb.ativos] == ["db"]
        sb = amb.sandbox
        r = sb.execute("timeout 5 bash -c '</dev/tcp/db/5432' && echo ok-db; "
                       "echo $SPRING_DATASOURCE_URL")
        assert "ok-db" in r.output and "jdbc:postgresql://db:5432/app" in r.output
        # arquivo escrito pelo agente cai no host, com o dono do host
        assert sb.write("/workspace/backend/A.txt", "oi").error is None
        assert (tmp_path / "backend" / "A.txt").read_text() == "oi"
        # pastas do deepagents na raiz são graváveis
        assert sb.write("/large_tool_results/x", "y").error is None
        # servidor em segundo plano sem redirecionar não trava o pipeline
        r = sb.execute("sleep 300 &", timeout=2)
        assert r.exit_code in (0, 124)
        rede = amb.rede
    r = subprocess.run(["docker", "network", "inspect", rede], capture_output=True)
    assert r.returncode != 0
