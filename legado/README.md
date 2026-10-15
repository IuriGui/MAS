# Legado

Código anterior ao sistema multiagente. Nada daqui é importado pelo pipeline
(`main.py`, `projeto.py`, `pipeline.py`) nem pelos testes. Fica guardado para
consulta e para o histórico do TCC.

| Caminho | O que é | Substituído por |
|---|---|---|
| `tools/manipulacao_de_arquivos.py` | Tools de arquivo (ler, escrever, listar, apagar) para um agente LangChain genérico, com sandbox em `FILE_TOOLS_BASE_DIR` (padrão `./workspace`). | `agents/ferramentas.py`: tools só de leitura sobre o ambiente compartilhado (`ambiente.py`). Quem grava é o orquestrador. |
| `scripts/demo_file_tools.py` | Demo do primeiro protótipo: um agente com as tools acima gerando diretórios de exemplo. | Os agentes do pipeline. |
| `scripts/arquiteto_chefe.py` | Atalho para rodar só o Arquiteto-chefe. | `python main.py --projeto exemplos/erp/sistema.md --so-contexto` |
| `exemplos/user.md` | Descrição do módulo Users usada nos primeiros testes. | `exemplos/erp/modulos/users.md`, de conteúdo idêntico. |

Para rodar algo daqui, use a raiz do repositório como diretório atual:

```bash
python -m legado.scripts.arquiteto_chefe
python -m legado.scripts.demo_file_tools   # cria ./workspace
```
