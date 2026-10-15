"""Configuração central. Troque o modelo pela variável de ambiente MODEL
(ex.: MODEL=openai:gpt-5.5) sem mexer no código."""
import os

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
CONTEXTO_PADRAO = os.environ.get(
    "CONTEXTO", os.path.join(os.path.dirname(__file__), "exemplos", "contexto_erp.json"))

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

# --- Autonomia dos agentes (F2) ---------------------------------------------
# 1 = agentes com tools de leitura do ambiente e submissão validada dentro do
# próprio loop; 0 = comportamento da linha de base (sem tools), para ablação.
AUTONOMIA = os.environ.get("AUTONOMIA", "1").lower() not in ("0", "false", "nao", "não")

# Orçamento de chamadas ao modelo por invocação de um agente autônomo (cada
# leitura do ambiente e cada submissão reprovada gastam uma chamada).
# Esgotado, o agente para e o orquestrador decide (nova tentativa ou falha).
LIMITE_CHAMADAS_AGENTE = max(2, int(os.environ.get("LIMITE_CHAMADAS_AGENTE", "12")))

# --- Comunicação entre agentes (F3) -----------------------------------------
# 1 = agentes podem consultar (ask_agent) e reportar bloqueios (report_blocker)
# aos donos dos artefatos anteriores do módulo; 0 = só a autonomia da F2.
# Só tem efeito com AUTONOMIA=1.
COMUNICACAO = os.environ.get("COMUNICACAO", "1").lower() not in ("0", "false", "nao", "não")

# Perguntas que um agente pode fazer por invocação.
MAX_CONSULTAS_AGENTE = max(0, int(os.environ.get("MAX_CONSULTAS_AGENTE", "3")))

# Revisões de artefatos (bloqueios atendidos) por módulo. Esgotado o limite, o
# bloqueio vira pendência para revisão humana e o módulo segue.
MAX_REVISOES_MODULO = max(0, int(os.environ.get("MAX_REVISOES_MODULO", "2")))
