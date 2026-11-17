"""Assistente opcional com LLM para perguntas sobre lotes fiscais, cache SHA-256 e rate-limit."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

from models.fiscal import ResultadoAnalise
from models.normalizadores import DECIMAL_ZERO
from reports.resumo import ResumoLote

DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_MAX_CHAMADAS_HORA = 20
JANELA_RATE_LIMIT_SEGUNDOS = 3600.0

SYSTEM_PROMPT_ASSISTENTE_FISCAL = (
    "Você é um assistente contábil e fiscal especializado em NF-e (55), NFC-e (65) e "
    "NFS-e Padrão Nacional do sistema nfe-local. "
    "Responda SEMPRE em português claro, objetivo e direto. "
    "Baseie-se ESTRITAMENTE nos números, totais, impostos e alertas já calculados "
    "pelo motor fiscal determinístico em Python fornecido no JSON de contexto. "
    "NUNCA invente valores, chaves ou notas que não estejam no contexto. "
    "Se uma informação não constar no resumo ou nas notas amostradas, informe isso explicitamente."
)


class ErroAssistenteLLM(Exception):
    """Exceção base do módulo de assistente LLM."""


class ErroLLMNaoConfigurado(ErroAssistenteLLM):
    """Levantada quando `LLM_API_KEY` não está configurada e não há cliente injetado."""


class ErroLimiteUsoLLM(ErroAssistenteLLM):
    """Levantada quando o limite de chamadas por hora ao provedor LLM é atingido."""

    def __init__(
        self,
        mensagem: str,
        *,
        chamadas_realizadas: int,
        max_chamadas_hora: int,
        segundos_para_liberar: int,
    ) -> None:
        super().__init__(mensagem)
        self.chamadas_realizadas = chamadas_realizadas
        self.max_chamadas_hora = max_chamadas_hora
        self.segundos_para_liberar = segundos_para_liberar


class ErroComunicacaoLLM(ErroAssistenteLLM):
    """Levantada quando a requisição HTTP ao provedor LLM falha ou retorna payload inválido."""


@dataclass(frozen=True, slots=True)
class ConfiguracaoLLM:
    """Parâmetros de configuração do provedor LLM carregados do ambiente."""

    api_key: str
    base_url: str
    modelo: str
    max_chamadas_hora: int

    @property
    def configurado(self) -> bool:
        """Indica se há chave de API configurada para chamadas externas."""
        return bool(self.api_key.strip())


@dataclass(frozen=True, slots=True)
class RespostaAssistenteLote:
    """Resultado de uma pergunta respondida pelo `ServicoAssistenteLote`."""

    pergunta: str
    resposta: str
    em_cache: bool
    modelo: str
    chamadas_restantes: int
    chave_cache: str


def carregar_configuracao_llm() -> ConfiguracaoLLM:
    """Lê as variáveis de ambiente `LLM_*` aplicando valores padrão seguros."""
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    base_url = (os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_LLM_BASE_URL).rstrip("/")
    modelo = os.environ.get("LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL

    raw_max = os.environ.get("LLM_MAX_CHAMADAS_HORA", "").strip()
    try:
        max_chamadas = max(1, int(raw_max)) if raw_max else DEFAULT_LLM_MAX_CHAMADAS_HORA
    except ValueError:
        max_chamadas = DEFAULT_LLM_MAX_CHAMADAS_HORA

    return ConfiguracaoLLM(
        api_key=api_key,
        base_url=base_url,
        modelo=modelo,
        max_chamadas_hora=max_chamadas,
    )


def normalizar_pergunta_llm(pergunta: str) -> str:
    """Normaliza a pergunta (`strip`, `lower` e colapso de espaços) para chave de cache."""
    return re.sub(r"\s+", " ", (pergunta or "").strip().lower())


def _prioridade_nota_para_contexto(resultado: ResultadoAnalise) -> tuple[int, int, Decimal]:
    """Ordena priorizando notas inválidas/com erro fatal, depois com avisos, e maior valor."""
    tem_erro = (
        0
        if (
            not resultado.valido
            or resultado.erro_fatal is not None
            or any(a.severidade == "ERRO" for a in resultado.alertas)
        )
        else 1
    )
    tem_aviso = 0 if resultado.alertas else 1
    valor = (
        resultado.documento.totais.valor_total_nota
        if resultado.documento is not None
        else DECIMAL_ZERO
    )
    return (tem_erro, tem_aviso, -valor)


def montar_contexto_lote_llm(
    resumo: ResumoLote,
    resultados: list[ResultadoAnalise],
    max_notas: int = 30,
) -> dict[str, Any]:
    """Monta dicionário enxuto e determinístico do lote (sem jamais incluir XML bruto)."""
    limite = max(1, max_notas)
    ordenados = sorted(resultados, key=_prioridade_nota_para_contexto)[:limite]

    notas_amostradas: list[dict[str, Any]] = []
    for res in ordenados:
        doc = res.documento
        if doc is not None:
            razao = doc.emitente.razao_social or ""
            cnpj_cpf = doc.emitente.documento or ""
            if razao and cnpj_cpf:
                emitente_str = f"{razao} ({cnpj_cpf})"
            else:
                emitente_str = razao or cnpj_cpf
            tipo_str = doc.tipo
            numero_str = doc.numero
            valor_str = f"{doc.totais.valor_total_nota:.2f}"
        else:
            emitente_str = ""
            tipo_str = "ERRO_XML"
            numero_str = ""
            valor_str = "0.00"

        notas_amostradas.append(
            {
                "arquivo_origem": res.arquivo_origem,
                "tipo": tipo_str,
                "numero": numero_str,
                "emitente": emitente_str,
                "valor_total": valor_str,
                "valido": res.valido,
                "alertas": [
                    {
                        "codigo": a.codigo,
                        "severidade": a.severidade,
                        "mensagem": a.mensagem,
                    }
                    for a in res.alertas
                ],
                "erro_fatal": res.erro_fatal,
            }
        )

    resumo_json = resumo.model_dump(mode="json")
    # Remove agrupamento diário caso exista apenas para manter o contexto ainda mais enxuto,
    # preservando contagem, totais financeiros, impostos_totais, por_emitente e por_mes.
    resumo_compacto = {
        "contagem": resumo_json.get("contagem", {}),
        "valor_produtos_servicos_total": resumo_json.get("valor_produtos_servicos_total", "0.00"),
        "valor_frete_total": resumo_json.get("valor_frete_total", "0.00"),
        "valor_seguro_total": resumo_json.get("valor_seguro_total", "0.00"),
        "valor_desconto_total": resumo_json.get("valor_desconto_total", "0.00"),
        "valor_outras_despesas_total": resumo_json.get("valor_outras_despesas_total", "0.00"),
        "valor_liquido_total": resumo_json.get("valor_liquido_total", "0.00"),
        "valor_total_notas": resumo_json.get("valor_total_notas", "0.00"),
        "impostos_totais": resumo_json.get("impostos_totais", {}),
        "por_emitente": resumo_json.get("por_emitente", []),
        "por_mes": resumo_json.get("por_mes", []),
    }

    return {
        "total_notas_no_lote": len(resultados),
        "max_notas_amostradas": limite,
        "resumo": resumo_compacto,
        "notas_amostradas": notas_amostradas,
    }


class CachePerguntasLLM:
    """Cache determinístico SHA-256 de perguntas e respostas sobre lotes fiscais."""

    def __init__(self, caminho_arquivo: Path | None = None) -> None:
        self._caminho_arquivo = caminho_arquivo
        self._lock = threading.Lock()
        self._dados: dict[str, str] = {}
        if self._caminho_arquivo is not None and self._caminho_arquivo.is_file():
            try:
                conteudo = json.loads(self._caminho_arquivo.read_text(encoding="utf-8"))
                if isinstance(conteudo, dict):
                    self._dados = {
                        str(k): str(v) for k, v in conteudo.items() if isinstance(v, str)
                    }
            except (OSError, json.JSONDecodeError):
                self._dados = {}

    @staticmethod
    def calcular_chave_cache(contexto: Mapping[str, Any], pergunta: str) -> str:
        """Calcula hash SHA-256 do JSON canônico (`sort_keys=True`) + pergunta normalizada."""
        contexto_canonico = json.dumps(
            contexto,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        pergunta_norm = normalizar_pergunta_llm(pergunta)
        payload = f"{contexto_canonico}\n---\n{pergunta_norm}".encode()
        return hashlib.sha256(payload).hexdigest()

    def obter(self, chave: str) -> str | None:
        """Retorna a resposta em cache para a chave SHA-256 ou `None`."""
        with self._lock:
            return self._dados.get(chave)

    def salvar(self, chave: str, resposta: str) -> None:
        """Armazena a resposta no cache em memória e (opcionalmente) em disco."""
        with self._lock:
            self._dados[chave] = resposta
            if self._caminho_arquivo is not None:
                try:
                    self._caminho_arquivo.parent.mkdir(parents=True, exist_ok=True)
                    self._caminho_arquivo.write_text(
                        json.dumps(self._dados, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError:
                    pass

    def limpar(self) -> None:
        """Remove todas as entradas do cache."""
        with self._lock:
            self._dados.clear()


class LimitadorUsoLLM:
    """Controlador de rate-limit em janela deslizante de 1 hora (3600 segundos)."""

    def __init__(
        self,
        max_chamadas_hora: int = DEFAULT_LLM_MAX_CHAMADAS_HORA,
        janela_segundos: float = JANELA_RATE_LIMIT_SEGUNDOS,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_chamadas_hora = max(1, max_chamadas_hora)
        self._janela_segundos = max(1.0, float(janela_segundos))
        self._relogio = relogio
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    @property
    def max_chamadas_hora(self) -> int:
        """Limite máximo de chamadas configurado por janela de 1 hora."""
        return self._max_chamadas_hora

    def _expurgar_antigas(self, agora: float) -> None:
        limite_inferior = agora - self._janela_segundos
        self._timestamps = [ts for ts in self._timestamps if ts > limite_inferior]

    def chamadas_restantes(self, max_chamadas_override: int | None = None) -> int:
        """Retorna quantas chamadas inéditas ainda podem ser feitas na janela atual."""
        limite = max(1, max_chamadas_override or self._max_chamadas_hora)
        with self._lock:
            agora = self._relogio()
            self._expurgar_antigas(agora)
            return max(0, limite - len(self._timestamps))

    def verificar_disponibilidade(self, max_chamadas_override: int | None = None) -> None:
        """Verifica se há quota disponível; caso contrário, levanta `ErroLimiteUsoLLM`."""
        limite = max(1, max_chamadas_override or self._max_chamadas_hora)
        with self._lock:
            agora = self._relogio()
            self._expurgar_antigas(agora)
            realizadas = len(self._timestamps)
            if realizadas >= limite:
                mais_antiga = self._timestamps[0]
                segundos_restantes = max(
                    1,
                    int(math.ceil((mais_antiga + self._janela_segundos) - agora)),
                )
                minutos_restantes = max(1, math.ceil(segundos_restantes / 60))
                raise ErroLimiteUsoLLM(
                    (
                        f"Limite de uso do assistente LLM atingido: {realizadas}/{limite} "
                        f"chamadas na última hora. Nova chamada liberada em ~{minutos_restantes} "
                        f"min ({segundos_restantes}s). Perguntas já em cache continuam disponíveis."
                    ),
                    chamadas_realizadas=realizadas,
                    max_chamadas_hora=limite,
                    segundos_para_liberar=segundos_restantes,
                )

    def registrar_chamada(self, max_chamadas_override: int | None = None) -> None:
        """Verifica disponibilidade e registra uma nova chamada na janela deslizante."""
        self.verificar_disponibilidade(max_chamadas_override=max_chamadas_override)
        with self._lock:
            self._timestamps.append(self._relogio())


ClienteCompletionCallable = Callable[[list[dict[str, str]], str, str, str], str]


class ServicoAssistenteLote:
    """Serviço de perguntas sobre o lote com contexto determinístico, cache e rate-limit."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        modelo: str | None = None,
        max_chamadas_hora: int | None = None,
        cache: CachePerguntasLLM | None = None,
        limitador: LimitadorUsoLLM | None = None,
        cliente_completion: ClienteCompletionCallable | None = None,
        timeout_segundos: float = 30.0,
    ) -> None:
        self._api_key_explicita = api_key
        self._base_url_explicita = base_url
        self._modelo_explicito = modelo
        self._max_chamadas_explicito = max_chamadas_hora
        self._cache = cache if cache is not None else CachePerguntasLLM()
        self._limitador = (
            limitador
            if limitador is not None
            else LimitadorUsoLLM(
                max_chamadas_hora=max_chamadas_hora or DEFAULT_LLM_MAX_CHAMADAS_HORA
            )
        )
        self._cliente_completion = cliente_completion
        self._timeout_segundos = timeout_segundos

    def _resolver_config(self) -> ConfiguracaoLLM:
        cfg_env = carregar_configuracao_llm()
        api_key = (
            self._api_key_explicita if self._api_key_explicita is not None else cfg_env.api_key
        )
        base_url = (
            self._base_url_explicita.rstrip("/")
            if self._base_url_explicita is not None
            else cfg_env.base_url
        )
        modelo = self._modelo_explicito if self._modelo_explicito is not None else cfg_env.modelo
        max_chamadas = (
            self._max_chamadas_explicito
            if self._max_chamadas_explicito is not None
            else cfg_env.max_chamadas_hora
        )
        return ConfiguracaoLLM(
            api_key=api_key,
            base_url=base_url,
            modelo=modelo,
            max_chamadas_hora=max_chamadas,
        )

    def esta_configurado(self) -> bool:
        """Retorna `True` se houver `LLM_API_KEY` definida ou callback `cliente_completion`."""
        if self._cliente_completion is not None:
            return True
        return self._resolver_config().configurado

    def obter_status(self) -> dict[str, Any]:
        """Retorna dicionário de status do assistente LLM para o endpoint `/api/llm/status`."""
        cfg = self._resolver_config()
        restantes = self._limitador.chamadas_restantes(max_chamadas_override=cfg.max_chamadas_hora)
        return {
            "configurado": self.esta_configurado(),
            "modelo": cfg.modelo,
            "chamadas_restantes": restantes,
            "max_chamadas_hora": cfg.max_chamadas_hora,
        }

    def _chamar_http_openai_compat(
        self,
        mensagens: list[dict[str, str]],
        modelo: str,
        base_url: str,
        api_key: str,
    ) -> str:
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": modelo,
            "messages": mensagens,
            "temperature": 0.1,
        }
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout_segundos,
            )
        except requests.RequestException as exc:
            raise ErroComunicacaoLLM(
                f"Falha de conexão ao consultar o provedor LLM em '{url}': {exc}"
            ) from exc

        if resp.status_code >= 400:
            detalhe = resp.text[:300]
            raise ErroComunicacaoLLM(f"Provedor LLM retornou HTTP {resp.status_code}: {detalhe}")

        try:
            dados = resp.json()
            conteudo = dados["choices"][0]["message"]["content"]
            if not isinstance(conteudo, str) or not conteudo.strip():
                raise ValueError("Campo 'content' vazio.")
            return conteudo.strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ErroComunicacaoLLM(
                "Resposta do provedor LLM em formato inesperado (esperado OpenAI chat/completions)."
            ) from exc

    def perguntar(
        self,
        *,
        resumo: ResumoLote,
        resultados: list[ResultadoAnalise],
        pergunta: str,
        max_notas_contexto: int = 30,
    ) -> RespostaAssistenteLote:
        """Responde a uma pergunta sobre o lote usando cache SHA-256 e respeitando rate-limit."""
        pergunta_limpa = (pergunta or "").strip()
        cfg = self._resolver_config()

        contexto = montar_contexto_lote_llm(
            resumo=resumo,
            resultados=resultados,
            max_notas=max_notas_contexto,
        )
        chave_cache = self._cache.calcular_chave_cache(contexto, pergunta_limpa)

        # 1. Verifica o cache antes de exigir configuração ou consumir rate-limit
        resposta_cache = self._cache.obter(chave_cache)
        if resposta_cache is not None:
            restantes = self._limitador.chamadas_restantes(
                max_chamadas_override=cfg.max_chamadas_hora
            )
            return RespostaAssistenteLote(
                pergunta=pergunta_limpa,
                resposta=resposta_cache,
                em_cache=True,
                modelo=cfg.modelo,
                chamadas_restantes=restantes,
                chave_cache=chave_cache,
            )

        # 2. Se não está em cache, exige provedor configurado
        if not self.esta_configurado():
            raise ErroLLMNaoConfigurado(
                "Assistente LLM não configurado. Defina a variável de ambiente LLM_API_KEY "
                "(e opcionalmente LLM_BASE_URL e LLM_MODEL) no arquivo .env para habilitar "
                "perguntas com IA sobre o lote."
            )

        # 3. Verifica disponibilidade no rate-limit antes de chamar a rede
        self._limitador.verificar_disponibilidade(max_chamadas_override=cfg.max_chamadas_hora)

        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT_ASSISTENTE_FISCAL},
            {
                "role": "user",
                "content": (
                    "Contexto determinístico do lote fiscal (JSON):\n"
                    f"{json.dumps(contexto, ensure_ascii=False, sort_keys=True)}\n\n"
                    f"Pergunta do usuário: {pergunta_limpa}"
                ),
            },
        ]

        executor = self._cliente_completion or self._chamar_http_openai_compat
        resposta_texto = executor(mensagens, cfg.modelo, cfg.base_url, cfg.api_key)

        # 4. Registra chamada bem-sucedida e grava no cache SHA-256
        self._limitador.registrar_chamada(max_chamadas_override=cfg.max_chamadas_hora)
        self._cache.salvar(chave_cache, resposta_texto)
        restantes = self._limitador.chamadas_restantes(max_chamadas_override=cfg.max_chamadas_hora)

        return RespostaAssistenteLote(
            pergunta=pergunta_limpa,
            resposta=resposta_texto,
            em_cache=False,
            modelo=cfg.modelo,
            chamadas_restantes=restantes,
            chave_cache=chave_cache,
        )
