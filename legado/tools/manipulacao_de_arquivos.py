"""
file_tools_v2.py

Versão otimizada do conjunto de ferramentas de sistema de arquivos para agentes
LangChain, com foco em:

1. Reduzir o número de ciclos LLM -> tool -> LLM através de operações em lote
   (write_files, read_files) para tarefas com múltiplos arquivos.
2. Reduzir a superfície de tools redundantes (create_directory removida —
   coberta implicitamente por write_file/write_files; file_exists e
   get_file_info fundidas em inspect_path).
3. Adicionar uma guarda de segurança (_safe_path) que restringe todas as
   operações a um diretório raiz (BASE_DIR), prevenindo path traversal
   (ex.: "../../etc/passwd") e delete/write acidental fora do workspace.
4. Retornar dados estruturados (JSON) nas tools cujo resultado alimenta
   decisões subsequentes do agente (inspect_path), reduzindo parsing
   ambíguo de linguagem natural pelo LLM.
5. Manter compatibilidade de import: `file_tools` continua existindo como
   lista pronta para uso.

Configuração:
    Defina a variável de ambiente FILE_TOOLS_BASE_DIR para apontar o
    workspace raiz. Se não definida, usa "./workspace" relativo ao diretório
    de execução do processo.
"""

import json
import os
import shutil
from datetime import datetime
from typing import List, Optional

from langchain_core.tools import tool

# --------------------------------------------------------------------------
# Configuração de diretório raiz (sandbox)
# --------------------------------------------------------------------------

BASE_DIR = os.path.abspath(os.environ.get("FILE_TOOLS_BASE_DIR", "./workspace"))
os.makedirs(BASE_DIR, exist_ok=True)


def _safe_path(path: str) -> str:
    """Resolve um path relativo ao BASE_DIR e garante que ele não escapa do sandbox.

    Levanta ValueError se o caminho resultante ficar fora de BASE_DIR
    (proteção contra path traversal, ex: '../../etc/passwd').
    """
    if os.path.isabs(path):
        # Trata paths absolutos como relativos ao BASE_DIR (remove a raiz)
        path = path.lstrip(os.sep)

    full = os.path.abspath(os.path.join(BASE_DIR, path))

    if full != BASE_DIR and not full.startswith(BASE_DIR + os.sep):
        raise ValueError(
            f"Acesso negado: '{path}' resolve para fora do diretório permitido "
            f"({BASE_DIR})."
        )
    return full


def _rel(full_path: str) -> str:
    """Converte um path absoluto interno de volta para path relativo ao BASE_DIR,
    para exibir mensagens mais legíveis ao agente/usuário."""
    try:
        rel = os.path.relpath(full_path, BASE_DIR)
        return rel if rel != "." else "."
    except ValueError:
        return full_path


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------


@tool
def read_file(path: str) -> str:
    """Lê e retorna o conteúdo de um arquivo de texto dentro do workspace.

    Args:
        path: Caminho do arquivo a ser lido (relativo ao workspace).

    Returns:
        Conteúdo do arquivo como string, ou mensagem de erro caso falhe.
    """
    try:
        full = _safe_path(path)
        with open(full, "r", encoding="utf-8") as f:
            return f.read()
    except ValueError as e:
        return f"Erro: {e}"
    except FileNotFoundError:
        return f"Erro: arquivo não encontrado em '{path}'."
    except Exception as e:
        return f"Erro ao ler o arquivo '{path}': {e}"


@tool
def read_files(paths: List[str]) -> str:
    """Lê múltiplos arquivos de texto em uma única chamada.

    Use esta ferramenta em vez de chamar read_file repetidamente quando
    precisar inspecionar vários arquivos de uma vez (ex.: revisar todos os
    arquivos de um módulo antes de refatorar).

    Args:
        paths: Lista de caminhos de arquivos a serem lidos (relativos ao workspace).

    Returns:
        String formatada com o conteúdo de cada arquivo, separado por
        cabeçalhos indicando o caminho, ou a mensagem de erro correspondente
        para arquivos que falharem.
    """
    blocos = []
    for path in paths:
        try:
            full = _safe_path(path)
            with open(full, "r", encoding="utf-8") as f:
                conteudo = f.read()
            blocos.append(f"--- {path} ---\n{conteudo}")
        except ValueError as e:
            blocos.append(f"--- {path} ---\nErro: {e}")
        except FileNotFoundError:
            blocos.append(f"--- {path} ---\nErro: arquivo não encontrado.")
        except Exception as e:
            blocos.append(f"--- {path} ---\nErro ao ler: {e}")
    return "\n\n".join(blocos)


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------


@tool
def write_file(path: str, content: str) -> str:
    """Cria ou sobrescreve um único arquivo de texto com o conteúdo fornecido.

    Cria diretórios intermediários automaticamente caso não existam. Para
    criar múltiplos arquivos de uma vez (ex.: gerar vários exemplos em uma
    mesma tarefa), prefira write_files, que evita múltiplos ciclos de
    chamada ao modelo.

    Args:
        path: Caminho do arquivo a ser criado/sobrescrito (relativo ao workspace).
        content: Conteúdo a ser gravado no arquivo.

    Returns:
        Mensagem indicando sucesso ou erro.
    """
    try:
        full = _safe_path(path)
        os.makedirs(os.path.dirname(full) or BASE_DIR, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Arquivo '{path}' gravado com sucesso ({len(content)} caracteres)."
    except ValueError as e:
        return f"Erro: {e}"
    except Exception as e:
        return f"Erro ao gravar o arquivo '{path}': {e}"


@tool
def write_files(files: List[dict]) -> str:
    """Cria ou sobrescreve múltiplos arquivos de texto em uma única chamada.

    Cria diretórios intermediários automaticamente para cada arquivo. Use
    esta ferramenta sempre que a tarefa envolver gerar mais de um arquivo
    (ex.: criar vários exemplos de código, popular uma pasta nova), em vez
    de chamar write_file repetidamente — isso reduz o número de ciclos de
    raciocínio do agente e a latência total da tarefa.

    Args:
        files: Lista de objetos no formato {"path": str, "content": str}.
               Cada item representa um arquivo a ser gravado.

    Returns:
        Relatório com uma linha de status (sucesso ou erro) por arquivo.
    """
    resultados = []
    for item in files:
        path = item.get("path")
        content = item.get("content", "")
        if not path:
            resultados.append("ERRO: item sem 'path' informado, ignorado.")
            continue
        try:
            full = _safe_path(path)
            os.makedirs(os.path.dirname(full) or BASE_DIR, exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            resultados.append(f"OK '{path}' ({len(content)} caracteres)")
        except ValueError as e:
            resultados.append(f"ERRO '{path}': {e}")
        except Exception as e:
            resultados.append(f"ERRO '{path}': {e}")
    return "\n".join(resultados)


@tool
def append_file(path: str, content: str) -> str:
    """Adiciona (anexa) conteúdo ao final de um arquivo existente.

    Se o arquivo não existir, ele é criado. Diretórios intermediários são
    criados automaticamente.

    Args:
        path: Caminho do arquivo (relativo ao workspace).
        content: Conteúdo a ser anexado.

    Returns:
        Mensagem indicando sucesso ou erro.
    """
    try:
        full = _safe_path(path)
        os.makedirs(os.path.dirname(full) or BASE_DIR, exist_ok=True)
        with open(full, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Conteúdo anexado com sucesso ao arquivo '{path}'."
    except ValueError as e:
        return f"Erro: {e}"
    except Exception as e:
        return f"Erro ao anexar conteúdo ao arquivo '{path}': {e}"


# --------------------------------------------------------------------------
# Exclusão / cópia / movimentação
# --------------------------------------------------------------------------


@tool
def delete_file(path: str) -> str:
    """Exclui um arquivo do workspace.

    Restrito ao diretório raiz do workspace (BASE_DIR) — não é possível
    excluir arquivos fora dele.

    Args:
        path: Caminho do arquivo a ser excluído (relativo ao workspace).

    Returns:
        Mensagem indicando sucesso ou erro.
    """
    try:
        full = _safe_path(path)
        os.remove(full)
        return f"Arquivo '{path}' excluído com sucesso."
    except ValueError as e:
        return f"Erro: {e}"
    except FileNotFoundError:
        return f"Erro: arquivo não encontrado em '{path}'."
    except Exception as e:
        return f"Erro ao excluir o arquivo '{path}': {e}"


@tool
def copy_file(source: str, destination: str) -> str:
    """Copia um arquivo de um caminho de origem para um caminho de destino,
    ambos dentro do workspace.

    Args:
        source: Caminho do arquivo de origem (relativo ao workspace).
        destination: Caminho de destino, incluindo nome do arquivo (relativo ao workspace).

    Returns:
        Mensagem indicando sucesso ou erro.
    """
    try:
        full_src = _safe_path(source)
        full_dst = _safe_path(destination)
        os.makedirs(os.path.dirname(full_dst) or BASE_DIR, exist_ok=True)
        shutil.copy2(full_src, full_dst)
        return f"Arquivo copiado de '{source}' para '{destination}' com sucesso."
    except ValueError as e:
        return f"Erro: {e}"
    except FileNotFoundError:
        return f"Erro: arquivo de origem não encontrado em '{source}'."
    except Exception as e:
        return f"Erro ao copiar arquivo de '{source}' para '{destination}': {e}"


@tool
def move_file(source: str, destination: str) -> str:
    """Move (ou renomeia) um arquivo dentro do workspace.

    Args:
        source: Caminho do arquivo de origem (relativo ao workspace).
        destination: Caminho de destino, incluindo nome do arquivo (relativo ao workspace).

    Returns:
        Mensagem indicando sucesso ou erro.
    """
    try:
        full_src = _safe_path(source)
        full_dst = _safe_path(destination)
        os.makedirs(os.path.dirname(full_dst) or BASE_DIR, exist_ok=True)
        shutil.move(full_src, full_dst)
        return f"Arquivo movido de '{source}' para '{destination}' com sucesso."
    except ValueError as e:
        return f"Erro: {e}"
    except FileNotFoundError:
        return f"Erro: arquivo de origem não encontrado em '{source}'."
    except Exception as e:
        return f"Erro ao mover arquivo de '{source}' para '{destination}': {e}"


# --------------------------------------------------------------------------
# Inspeção (listar / existência / metadados)
# --------------------------------------------------------------------------


@tool
def list_directory(path: str = ".") -> str:
    """Lista os arquivos e subpastas contidos em um diretório do workspace.

    Args:
        path: Caminho do diretório a ser listado, relativo ao workspace
              (padrão: raiz do workspace).

    Returns:
        Lista formatada de itens do diretório, ou mensagem de erro.
    """
    try:
        full = _safe_path(path)
        items: List[str] = os.listdir(full)
        if not items:
            return f"O diretório '{path}' está vazio."

        linhas = []
        for item in sorted(items):
            item_full = os.path.join(full, item)
            tipo = "DIR " if os.path.isdir(item_full) else "FILE"
            linhas.append(f"[{tipo}] {item}")
        return "\n".join(linhas)
    except ValueError as e:
        return f"Erro: {e}"
    except FileNotFoundError:
        return f"Erro: diretório não encontrado em '{path}'."
    except Exception as e:
        return f"Erro ao listar o diretório '{path}': {e}"


@tool
def inspect_path(path: str) -> str:
    """Verifica se um caminho existe no workspace e retorna seus metadados.

    Combina a checagem de existência com informações de tamanho e data de
    modificação em uma única chamada (substitui o uso separado de
    'verificar se existe' + 'obter informações do arquivo').

    Args:
        path: Caminho a ser inspecionado (relativo ao workspace).

    Returns:
        JSON com os campos: exists (bool), type ("file"|"directory"|None),
        size_bytes (int|None), modified (str|None), error (str|None).
    """
    try:
        full = _safe_path(path)
    except ValueError as e:
        return json.dumps({"exists": False, "type": None, "size_bytes": None,
                            "modified": None, "error": str(e)})

    if not os.path.exists(full):
        return json.dumps({"exists": False, "type": None, "size_bytes": None,
                            "modified": None, "error": None})

    tipo = "directory" if os.path.isdir(full) else "file"
    try:
        stat = os.stat(full)
        modificado = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        tamanho = stat.st_size if tipo == "file" else None
        return json.dumps({
            "exists": True,
            "type": tipo,
            "size_bytes": tamanho,
            "modified": modificado,
            "error": None,
        })
    except Exception as e:
        return json.dumps({"exists": True, "type": tipo, "size_bytes": None,
                            "modified": None, "error": str(e)})


# --------------------------------------------------------------------------
# Lista pronta para uso em agentes LangChain
# --------------------------------------------------------------------------

file_tools = [
    read_file,
    read_files,
    write_file,
    write_files,
    append_file,
    delete_file,
    list_directory,
    inspect_path,
    copy_file,
    move_file,
]