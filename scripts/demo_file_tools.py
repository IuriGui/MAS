"""Demo antiga (agente.py) isolada: um agente com as tools de arquivo.
Rode com:  python -m scripts.demo_file_tools
"""
import time

from langchain.agents import create_agent

from config import MODEL
from tools import manipulacao_de_arquivos


if __name__ == "__main__":
    inicio = time.time()


    agent = create_agent(model=MODEL, tools=manipulacao_de_arquivos.file_tools)
    resultado = agent.invoke({"messages": [{"role": "user", "content": (
        "Use as tools para gerar dois diretórios: um com exemplos de "
        "recursividade e outro com algoritmos de busca em grafos."
    )}]})
    print(resultado["messages"][-1].content)
    print(f"{time.time() - inicio:.1f}s")
