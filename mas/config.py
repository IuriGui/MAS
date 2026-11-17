"""Configuração central. Troque o modelo pela variável de ambiente MODEL
(ex.: MODEL=openai:gpt-5.5) sem mexer no código."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("MODEL", "google_genai:gemini-3.8-flash")

# Quantas vezes re-executar um agente quando a validação em código falhar
MAX_TENTATIVAS = max(1, int(os.environ.get("MAX_TENTATIVAS", "2")))

# Liga/desliga a validação em código das saídas (VALIDAR=0 para desligar)
VALIDAR = os.environ.get("VALIDAR", "1").lower() not in ("0", "false", "nao", "não")

# Onde os artefatos de cada módulo são gravados
SAIDA_DIR = os.environ.get("SAIDA_DIR", "saida")

# PROJECT CONTEXT usado quando um módulo roda sozinho (sem o Arquiteto-chefe)
RAIZ = Path(__file__).resolve().parent.parent
CONTEXTO_PADRAO = os.environ.get(
    "CONTEXTO", str(RAIZ / "exemplos" / "erp" / "contexto.json"))

# Quantos arquivos cada chamada de Implementador gera
IMPL_LOTE = max(1, int(os.environ.get("IMPL_LOTE", "5")))

# Limite (caracteres) do conteúdo integral de dependências anexado a cada lote.
# Acima dele, os arquivos seguintes vão só com as assinaturas públicas.
# (60 mil era pouco: na 2ª execução real cortou os repositórios do lote de
# serviços, e o modelo inventou nomes de métodos.) ~400 mil caracteres ≈ 100 mil tokens.
IMPL_LIMITE_CONTEXTO = int(os.environ.get("IMPL_LIMITE_CONTEXTO", "400000"))

# --- Limites contra repetição sem fim -------------------------------------
# O create_agent do LangChain usa recursion_limit=9999: se o modelo erra o
# formato da saída estruturada, ele repete a chamada até lá. Este é o máximo de
# passos do grafo por invocação = máximo de chamadas ao modelo por tentativa.
# 2 = só a resposta; 3 = resposta + 1 correção de formato interna do LangChain.
# Pior caso por etapa (ou lote): MAX_TENTATIVAS * LIMITE_PASSOS_AGENTE chamadas.
LIMITE_PASSOS_AGENTE = max(2, int(os.environ.get("LIMITE_PASSOS_AGENTE", "3")))

# Repetições da biblioteca em erro HTTP (rate limit, 5xx) e timeout por chamada
MAX_RETRIES_API = max(0, int(os.environ.get("MAX_RETRIES_API", "3")))
TIMEOUT_API = float(os.environ.get("TIMEOUT_API", "600"))

# --- Sandbox Docker dos Implementadores (agentes/sandbox.py) ----------
# Com SANDBOX=1 os Implementadores são deep agents: escrevem os arquivos dentro
# de um container (imagem SANDBOX_IMAGEM, com JDK, Maven e Node), compilam e
# testam antes de entregar. O banco e o cache do tech_stack sobem na mesma rede
# Docker. SANDBOX=0 volta aos agentes só de saída estruturada.
SANDBOX = os.environ.get("SANDBOX", "1").lower() not in ("0", "false", "nao", "não")
SANDBOX_IMAGEM = os.environ.get("SANDBOX_IMAGEM", "dockersandbox")
# Portas do sandbox publicadas em 127.0.0.1 do host (ex.: 5173 do Vite, 8080 da API)
SANDBOX_PORTAS = [int(p) for p in os.environ.get("SANDBOX_PORTAS", "5173").split(",")
                  if p.strip()]
# Passos do grafo por tentativa no modo sandbox (cada ciclo modelo -> tool são
# alguns passos). Substitui LIMITE_PASSOS_AGENTE para os Implementadores.
# Era 250; baixado para limitar o pior caso de custo (relato 9). Ao estourar, o
# que já foi gravado é colhido e validado do mesmo jeito.
LIMITE_PASSOS_SANDBOX = max(10, int(os.environ.get("LIMITE_PASSOS_SANDBOX", "120")))
# Lotes por execução em cada etapa de implementação (0 = sem limite). Ao atingir,
# a etapa para com os lotes aprovados salvos; uma nova execução retoma dali.
LOTES_POR_EXECUCAO = max(0, int(os.environ.get("LOTES_POR_EXECUCAO", "0")))
