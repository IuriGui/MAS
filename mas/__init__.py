"""Sistema multiagentes: da descrição do sistema ao código.

- agentes/   como cada agente é criado, o que ele recebe e onde roda
- pipeline/  orquestração: projeto -> módulos -> etapas, com validação e retry
- codigo/    o código gerado, tratado em código (sem LLM): layout, DDL, disco
- schemas.py contratos entre agentes · validacao.py regras de cada etapa
- relatorio.py artefatos JSON -> markdown · config.py variáveis de ambiente
"""
