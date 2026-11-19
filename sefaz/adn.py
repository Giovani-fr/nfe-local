"""Cliente REST do ADN Contribuintes (NFS-e Padrão Nacional) com mTLS (`requests-pkcs12`).

Contrato confirmado no Swagger oficial e resumido em `docs/adn.md`. O mesmo certificado A1 do
`NFeDistribuicaoDFe` é reaproveitado. O `.pfx` é lido em memória, mas o `requests-pkcs12` grava por
instantes um `.pem` temporário (chave cifrada por senha aleatória) na pasta temporária e o
apaga em seguida (ver a tarefa 28 do backlog).
"""

from __future__ import annotations

import errno as _errno
import json
import os
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import requests
from pydantic import BaseModel, ConfigDict, Field
from requests_pkcs12 import Pkcs12Adapter

from models.fiscal import DocumentoFiscal
from models.normalizadores import limpar_documento, para_data_hora
from parsers.xml_seguro import ErroParseXML, parse_xml_seguro, primeiro, texto
from sefaz.cert_a1 import extrair_metadados_certificado

# O Swagger não traz `servers`; o prefixo `/contribuintes` vem do endereço da documentação
# (`.../contribuintes/docs`). Ver "Não confirmado" em docs/adn.md.
URL_ADN_PRODUCAO = "https://adn.nfse.gov.br/contribuintes"
URL_ADN_HOMOLOGACAO = "https://adn.producaorestrita.nfse.gov.br/contribuintes"

TIPOS_DOCUMENTO_GUARDADOS = frozenset({"NFSE", "EVENTO"})
CODIGOS_ERRO_CERTIFICADO = frozenset({"E2210", "E2211", "E2212", "E2213", "E2214"})

# Escolhas do projeto (o Swagger não documenta bloqueio nem limite de uso).
ESPERA_PADRAO_LIMITE_SEGUNDOS = 3600
ESPERA_PADRAO_INDISPONIVEL_SEGUNDOS = 600

Papel = Literal["PRESTADOR", "TOMADOR", "INTERMEDIARIO"]
CategoriaErroADN = Literal[
    "LIMITE", "INDISPONIVEL", "CONEXAO", "RESPOSTA_INVALIDA", "ACESSO", "ENDERECO"
]

VARIAVEL_URL_BASE = "NFE_ADN_URL_BASE"
# Códigos documentados de "nenhum documento" para GET /DFe/{NSU} (HTTP 404).
CODIGOS_FILA_ZERADA = frozenset({"E2220", "E2230"})


class FalhaConexao(BaseModel):
    """Causa de uma falha de conexão, sem URL, CNPJ, caminho, senha nem mensagens brutas."""

    model_config = ConfigDict(extra="ignore")

    etapa: str
    excecao_raiz: str
    cadeia_excecoes: list[str] = Field(default_factory=list)
    codigo_so: str | None = None
    motivo_tls: str | None = None
    servidor_encerrou_conexao: bool = False
    tempo_esgotado: bool = False
    orientacao: str = ""


_RE_MOTIVO_TLS = re.compile(r"^[A-Z0-9_]{3,60}$")
_ENCERRAMENTO = {
    "RemoteDisconnected",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "BrokenPipeError",
    "SSLEOFError",
}
_TIMEOUTS = {
    "ConnectTimeout",
    "ReadTimeout",
    "ConnectTimeoutError",
    "ReadTimeoutError",
    "TimeoutError",
    "timeout",
}
_ORIENTACAO_ENCERROU = (
    "o ADN pode não ter aceitado o certificado ou o endereço de rede. Tente outra rede e "
    "confira se outro sistema usa o mesmo certificado ao mesmo tempo."
)


def _cadeia_excecoes(raiz: BaseException) -> list[tuple[BaseException, int]]:
    """Percorre `__cause__`, `__context__`, `.reason` e `args` (em largura, sem repetir)."""
    vistos: set[int] = set()
    fila: list[tuple[BaseException, int]] = [(raiz, 0)]
    saida: list[tuple[BaseException, int]] = []
    while fila:
        exc, profundidade = fila.pop(0)
        if id(exc) in vistos:
            continue
        vistos.add(id(exc))
        saida.append((exc, profundidade))
        candidatos = [exc.__cause__, exc.__context__, getattr(exc, "reason", None)]
        candidatos += list(getattr(exc, "args", ()) or ())
        fila.extend((c, profundidade + 1) for c in candidatos if isinstance(c, BaseException))
    return saida


def _codigo_so(cadeia: list[tuple[BaseException, int]]) -> str | None:
    for exc, _ in reversed(cadeia):
        if not isinstance(exc, OSError):
            continue
        winerror = getattr(exc, "winerror", None)
        if winerror:
            return f"WinError {winerror}"
        if exc.errno:
            simbolo = _errno.errorcode.get(exc.errno)
            return f"errno {exc.errno}" + (f" ({simbolo})" if simbolo else "")
    return None


def analisar_falha_conexao(exc: BaseException, servico: str = "o ADN") -> FalhaConexao:
    """Classifica a falha: etapa, exceção raiz, código do SO, encerramento/timeout e orientação."""
    cadeia = _cadeia_excecoes(exc)
    nomes: set[str] = set()
    for e, _ in cadeia:
        nomes |= {c.__name__ for c in type(e).__mro__}
    mensagens = " ".join(str(e).lower() for e, _ in cadeia)
    maior = max(p for _, p in cadeia)
    raiz = [e for e, p in cadeia if p == maior][-1]
    motivo = next(
        (
            e.reason
            for e, _ in cadeia
            if isinstance(getattr(e, "reason", None), str) and _RE_MOTIVO_TLS.match(e.reason)
        ),
        None,
    )

    encerrou = bool(nomes & _ENCERRAMENTO) or "eof occurred" in mensagens
    # `NewConnectionError` herda de `ConnectTimeoutError` no urllib3: só conta o tipo exato.
    timeout = (
        any(isinstance(e, TimeoutError) or type(e).__name__ in _TIMEOUTS for e, _ in cadeia)
        or "timed out" in mensagens
    )
    handshake_tls = bool(nomes & {"SSLError", "SSLEOFError"}) or "handshake" in mensagens

    if nomes & {"NameResolutionError", "gaierror"} or "getaddrinfo failed" in mensagens:
        etapa = "resolução de nome"
        orientacao = (
            "O nome do servidor não foi resolvido. Verifique a internet, o DNS e VPN/proxy."
        )
    elif handshake_tls and timeout:
        etapa = "handshake TLS"
        orientacao = "O handshake TLS demorou demais. Aumente --timeout ou tente outra rede."
    elif handshake_tls:
        etapa = "handshake TLS"
        orientacao = (
            f"O servidor encerrou a conexão no handshake TLS: {_ORIENTACAO_ENCERROU}"
            if encerrou
            else "A negociação TLS falhou. Confira o certificado A1 (validade e senha) e a data "
            "do computador."
        )
    elif nomes & {"ReadTimeout", "ReadTimeoutError"}:
        etapa = "leitura da resposta"
        orientacao = "O servidor não respondeu a tempo. Aumente --timeout ou tente mais tarde."
    elif timeout:
        etapa = "conexão TCP"
        orientacao = (
            "A conexão com o servidor (porta 443) demorou demais. Verifique firewall, proxy e VPN, "
            "ou aumente --timeout."
        )
    elif nomes & {"ConnectionRefusedError", "NewConnectionError"}:
        etapa = "conexão TCP"
        orientacao = (
            "A conexão com o servidor (porta 443) foi recusada ou não alcançou o destino. "
            "Verifique firewall, proxy e VPN, e tente outra rede."
        )
    elif "RemoteDisconnected" in nomes:
        etapa = "leitura da resposta"
        orientacao = (
            "A conexão TLS foi estabelecida, mas o servidor a encerrou sem responder: "
            + _ORIENTACAO_ENCERROU
        )
    elif encerrou:
        etapa = "handshake TLS (provável)"
        orientacao = (
            f"O servidor encerrou a conexão (reset) durante o handshake TLS: {_ORIENTACAO_ENCERROU}"
        )
    else:
        etapa = "não identificada"
        orientacao = (
            "Falha de conexão não classificada. Tente novamente, em outra rede se possível."
        )

    orientacao = orientacao.replace("o ADN", servico)
    return FalhaConexao(
        etapa=etapa,
        excecao_raiz=type(raiz).__name__,
        cadeia_excecoes=[type(e).__name__ for e, _ in cadeia],
        codigo_so=_codigo_so(cadeia),
        motivo_tls=motivo,
        servidor_encerrou_conexao=encerrou,
        tempo_esgotado=timeout,
        orientacao=orientacao,
    )


def mensagem_falha_conexao(falha: FalhaConexao, tentativas: int | None = None) -> str:
    """Texto para falha de conexão: 1) falha intermitente é comum, 2) rede/certificado."""
    primeira = f"Falha de conexão com o ADN (etapa: {falha.etapa}; {falha.excecao_raiz}). "
    primeira += "Falhas intermitentes do ADN são comuns. "
    if tentativas:
        vezes = "vez" if tentativas == 1 else "vezes"
        primeira += f"O programa já tentou {tentativas} {vezes}. "
    primeira += "Vale tentar de novo em alguns minutos."
    return f"{primeira} Se continuar: {falha.orientacao}"


TENTATIVAS_PADRAO = 3
TENTATIVAS_MAXIMO = 5
VARIAVEL_TENTATIVAS = "NFE_ADN_TENTATIVAS"
# Espera crescente (segundos) antes da 2ª, 3ª, 4ª... tentativa; a última se repete.
ESPERAS_RETENTATIVA_PADRAO: tuple[float, ...] = (2.0, 5.0, 10.0)


def resolver_tentativas(explicita: int | None = None) -> int:
    """Tentativas por consulta (1 a 5): argumento, `NFE_ADN_TENTATIVAS` ou 3."""
    bruto: int | str | None = explicita
    if bruto is None:
        bruto = os.environ.get(VARIAVEL_TENTATIVAS, "").strip() or None
    if bruto is None:
        return TENTATIVAS_PADRAO
    try:
        valor = int(bruto)
    except (TypeError, ValueError):
        valor = 0
    if not 1 <= valor <= TENTATIVAS_MAXIMO:
        raise ValueError(
            f"O número de tentativas deve ser um inteiro de 1 a {TENTATIVAS_MAXIMO} "
            f"(--tentativas ou {VARIAVEL_TENTATIVAS})."
        )
    return valor


class ErroADN(Exception):
    """Falha de comunicação ou resposta inesperada do ADN (a rejeição documentada não é erro)."""

    def __init__(
        self,
        mensagem: str,
        *,
        categoria: CategoriaErroADN,
        http_status: int | None = None,
        retry_apos_segundos: int | None = None,
        falha: FalhaConexao | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.falha = falha
        self.categoria = categoria
        self.http_status = http_status
        self.retry_apos_segundos = retry_apos_segundos


class ErroBloqueioADN(Exception):
    """Consulta recusada localmente porque o intervalo de espera ainda não terminou."""

    def __init__(self, mensagem: str, *, bloqueado_ate: datetime, segundos_restantes: int) -> None:
        super().__init__(mensagem)
        self.bloqueado_ate = bloqueado_ate
        self.segundos_restantes = segundos_restantes


class MensagemADN(BaseModel):
    model_config = ConfigDict(extra="ignore")

    codigo: str = ""
    descricao: str = ""
    complemento: str = ""


class ItemADN(BaseModel):
    """Item de `LoteDFe` (`DistribuicaoNSU` no Swagger)."""

    model_config = ConfigDict(extra="ignore")

    nsu: int | None = None
    chave_acesso: str = ""
    tipo_documento: str = "NENHUM"
    tipo_evento: str | None = None
    arquivo_xml: str | None = None
    data_hora_geracao: datetime | None = None


class RespostaADN(BaseModel):
    """Resposta de `GET /DFe/{NSU}` normalizada."""

    model_config = ConfigDict(extra="ignore")

    http_status: int
    tipo_consulta: str | None = None
    status_processamento: str
    lote: list[ItemADN] = Field(default_factory=list)
    alertas: list[MensagemADN] = Field(default_factory=list)
    erros: list[MensagemADN] = Field(default_factory=list)
    tipo_ambiente: str | None = None
    versao_aplicativo: str | None = None


class EstadoCursorADN(BaseModel):
    """Cursor do ADN: NSU inteiro (int64), independente do cursor do `NFeDistribuicaoDFe`."""

    model_config = ConfigDict(extra="ignore")

    ambiente: Literal[1, 2] = 2
    ultimo_nsu: int = 0
    ultima_consulta: datetime | None = None
    bloqueado_ate: datetime | None = None
    ultimo_status: str | None = None
    ultimo_motivo: str | None = None
    # CNPJ consultado na última execução (o cursor é por CNPJ). Nunca sai em JSON/API.
    documento_consulta: str = Field(default="", exclude=True)


# --- Interpretação da resposta ----------------------------------------------------------------


def _chave_ci(dados: dict[str, Any], nome: str) -> Any:
    """Busca insensível a maiúsculas (o Swagger usa PascalCase; tolera camelCase)."""
    alvo = nome.lower()
    for chave, valor in dados.items():
        if str(chave).lower() == alvo:
            return valor
    return None


def _mensagens(bruto: Any) -> list[MensagemADN]:
    saida: list[MensagemADN] = []
    if not isinstance(bruto, list):
        return saida
    for m in bruto:
        if isinstance(m, dict):
            saida.append(
                MensagemADN(
                    codigo=str(_chave_ci(m, "Codigo") or ""),
                    descricao=str(_chave_ci(m, "Descricao") or ""),
                    complemento=str(_chave_ci(m, "Complemento") or ""),
                )
            )
    return saida


def _int_ou_none(valor: Any) -> int | None:
    try:
        return int(valor) if valor is not None and str(valor).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _status_por_http(http_status: int) -> str:
    if http_status == 404:
        return "NENHUM_DOCUMENTO_LOCALIZADO"
    if 200 <= http_status < 300:
        return "DOCUMENTOS_LOCALIZADOS"
    return "REJEICAO"


def parse_resposta_adn(http_status: int, corpo: bytes | str) -> RespostaADN:
    """Converte o JSON do ADN em `RespostaADN`. Levanta `ErroADN` se o corpo não for JSON."""
    texto_corpo = corpo.decode("utf-8", errors="replace") if isinstance(corpo, bytes) else corpo
    try:
        dados = json.loads(texto_corpo) if texto_corpo.strip() else {}
    except ValueError as exc:
        raise ErroADN(
            f"O ADN respondeu (HTTP {http_status}) com um corpo que não é JSON.",
            categoria="RESPOSTA_INVALIDA",
            http_status=http_status,
        ) from exc
    if not isinstance(dados, dict):
        raise ErroADN(
            f"O ADN respondeu (HTTP {http_status}) com um JSON em formato inesperado.",
            categoria="RESPOSTA_INVALIDA",
            http_status=http_status,
        )

    lote: list[ItemADN] = []
    for bruto in _chave_ci(dados, "LoteDFe") or []:
        if not isinstance(bruto, dict):
            continue
        evento = _chave_ci(bruto, "TipoEvento")
        lote.append(
            ItemADN(
                nsu=_int_ou_none(_chave_ci(bruto, "NSU")),
                chave_acesso=str(_chave_ci(bruto, "ChaveAcesso") or ""),
                tipo_documento=str(_chave_ci(bruto, "TipoDocumento") or "NENHUM").upper(),
                tipo_evento=str(evento).upper() if evento else None,
                arquivo_xml=_chave_ci(bruto, "ArquivoXml"),
                data_hora_geracao=para_data_hora(str(_chave_ci(bruto, "DataHoraGeracao") or "")),
            )
        )

    status = str(_chave_ci(dados, "StatusProcessamento") or "").upper() or _status_por_http(
        http_status
    )
    return RespostaADN(
        http_status=http_status,
        tipo_consulta=_chave_ci(dados, "TipoConsulta"),
        status_processamento=status,
        lote=lote,
        alertas=_mensagens(_chave_ci(dados, "Alertas")),
        erros=_mensagens(_chave_ci(dados, "Erros")),
        tipo_ambiente=_chave_ci(dados, "TipoAmbiente"),
        versao_aplicativo=_chave_ci(dados, "VersaoAplicativo"),
    )


_MENSAGENS_POR_CODIGO: dict[str, str] = {
    "E2210": "O ADN considerou o certificado digital inválido.",
    "E2211": "O ADN informou que o certificado digital foi revogado.",
    "E2212": "O ADN encontrou erro na cadeia de certificação do certificado digital.",
    "E2213": "O certificado digital está expirado. Instale um certificado A1 válido.",
    "E2214": "O ADN não conseguiu consultar a lista de revogação do certificado (LCR). "
    "Tente novamente mais tarde.",
    "E2243": "O CNPJ de consulta precisa ter o mesmo CNPJ base (8 primeiros dígitos) do "
    "certificado. Para consultar outro CNPJ é preciso procuração.",
    "E2244": "Foram enviados CNPJ e CPF de consulta ao mesmo tempo; informe apenas um.",
    "E2245": "Não há procuração do CNPJ consultado para o titular do certificado. "
    "A procuração é cadastrada pelo outorgante no portal da Receita Federal.",
    "E2246": "O CNPJ de consulta é inválido (dígito verificador). Confira NFE_CNPJ_CONSULTA.",
    "E2247": "O CPF de consulta é inválido (dígito verificador).",
}

MENSAGEM_REJEICAO_SEM_DETALHES = (
    "O ADN rejeitou a consulta (HTTP 400), mas a resposta veio sem detalhes do motivo. "
    "Isso não indica, por si só, problema no certificado. Confira o CNPJ de consulta "
    "(NFE_CNPJ_CONSULTA ou o CNPJ do certificado) e tente novamente mais tarde."
)


def mensagem_rejeicao(resposta: RespostaADN) -> str:
    """Texto simples em português para uma rejeição (`REJEICAO`), sem expor dados da consulta."""
    if not resposta.erros:
        return MENSAGEM_REJEICAO_SEM_DETALHES
    partes: list[str] = []
    for erro in resposta.erros:
        cod = erro.codigo.strip().upper()
        if cod in _MENSAGENS_POR_CODIGO:
            partes.append(f"{_MENSAGENS_POR_CODIGO[cod]} ({cod})")
        elif cod == "E999":
            partes.append(
                "O ADN registrou uma falha não catalogada. Se persistir, informe ao suporte do "
                f"ADN este identificador: {erro.descricao or 'não informado'} (E999)."
            )
        else:
            detalhe = erro.descricao or "sem descrição"
            partes.append(f"Rejeição {cod or 'sem código'}: {detalhe}")
    return " ".join(partes)


# --- Papel do CNPJ na nota ---------------------------------------------------------------------


def extrair_documento_intermediario(xml_bytes: bytes) -> str:
    """Documento (CNPJ/CPF) do intermediário (`<interm>` da DPS), ou string vazia."""
    try:
        raiz = parse_xml_seguro(xml_bytes)
    except ErroParseXML:
        return ""
    el = primeiro(raiz, "interm")
    if el is None:
        return ""
    return limpar_documento(texto(el, "CNPJ") or texto(el, "CPF"))


PAPEL_PARA_VISAO = {
    "PRESTADOR": "EMITIDA",
    "TOMADOR": "RECEBIDA",
    "INTERMEDIARIO": "INTERMEDIARIA",
}
PAPEIS_VISAO = ("EMITIDA", "RECEBIDA", "INTERMEDIARIA")


def calcular_papel_visao(
    documento_alvo: str | None,
    emitente_documento: str,
    destinatario_documento: str,
    papel_adn: str | None = None,
) -> str | None:
    """EMITIDA, RECEBIDA ou INTERMEDIARIA para exibição.

    O ADN já traz `PRESTADOR`/`TOMADOR`/`INTERMEDIARIO`; para as demais origens compara o
    documento consultado com o emitente e o destinatário (sem ler XML).
    """
    if papel_adn in PAPEL_PARA_VISAO:
        return PAPEL_PARA_VISAO[papel_adn]
    alvo = limpar_documento(documento_alvo or "")
    if not alvo:
        return None
    if limpar_documento(emitente_documento) == alvo:
        return "EMITIDA"
    if limpar_documento(destinatario_documento) == alvo:
        return "RECEBIDA"
    return None


def calcular_papel(
    documento_alvo: str,
    doc: DocumentoFiscal,
    xml_bytes: bytes = b"",
) -> Papel | None:
    """Papel do documento consultado na nota: PRESTADOR, TOMADOR ou INTERMEDIARIO.

    Compara apenas caracteres alfanuméricos em maiúsculas (suporta CNPJ alfanumérico). Se o mesmo
    documento ocupar mais de um papel, vale a ordem prestador, tomador, intermediário.
    """
    alvo = limpar_documento(documento_alvo)
    if not alvo:
        return None
    if limpar_documento(doc.emitente.documento) == alvo:
        return "PRESTADOR"
    if doc.destinatario is not None and limpar_documento(doc.destinatario.documento) == alvo:
        return "TOMADOR"
    if xml_bytes and extrair_documento_intermediario(xml_bytes) == alvo:
        return "INTERMEDIARIO"
    return None


# --- Cliente HTTP ------------------------------------------------------------------------------


def _segundos_retry_after(valor: str | None) -> int | None:
    if not valor:
        return None
    valor = valor.strip()
    if valor.isdigit():
        return max(1, int(valor))
    try:
        alvo = parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        return None
    if alvo.tzinfo is None:
        alvo = alvo.replace(tzinfo=UTC)
    return max(1, int((alvo - datetime.now(UTC)).total_seconds()))


def _info_tls(resp: Any) -> tuple[str | None, str | None] | None:
    """`(versão, cifrador)` do TLS negociado, se a conexão ainda estiver acessível."""
    try:
        bruto = resp.raw
        conexao = getattr(bruto, "_connection", None) or getattr(bruto, "connection", None)
        sock = conexao.sock
        cifrador = sock.cipher()
        return sock.version(), (cifrador[0] if cifrador else None)
    except Exception:  # noqa: BLE001 - melhor esforço; nunca derruba o diagnóstico
        return None


class ClienteADN:
    """Cliente `GET /DFe/{NSU}` com mTLS em memória e `cnpjConsulta` sempre presente."""

    def __init__(
        self,
        *,
        pfx_bytes: bytes,
        senha: str,
        ambiente: Literal[1, 2] = 2,
        cnpj_consulta: str | None = None,
        sessao_http: Any | None = None,
        timeout_segundos: float = 30.0,
        base_url: str | None = None,
    ) -> None:
        self._pfx_bytes = pfx_bytes
        self._senha = senha
        self._metadados = extrair_metadados_certificado(pfx_bytes, senha)
        self._ambiente: Literal[1, 2] = 1 if ambiente == 1 else 2
        self._sessao_http = sessao_http
        self._timeout = timeout_segundos
        self._base_url = resolver_url_base(self._ambiente, base_url)
        self._cnpj = resolver_cnpj_consulta(cnpj_consulta, self._metadados.documento)
        self.ultima_info_tls: tuple[str | None, str | None] | None = None

    @property
    def cnpj_consulta(self) -> str:
        return self._cnpj

    @property
    def timeout_segundos(self) -> float:
        return self._timeout

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def metadados_certificado(self):
        return self._metadados

    def url_nsu(self, nsu: int) -> str:
        """URL do recurso consultado, sem query string (nada sensível)."""
        return f"{self._base_url}/DFe/{int(nsu)}"

    @property
    def ambiente(self) -> Literal[1, 2]:
        return self._ambiente

    def _criar_sessao_mtls(self) -> requests.Session:
        sessao = requests.Session()
        sessao.mount(
            "https://",
            Pkcs12Adapter(
                pkcs12_data=self._pfx_bytes,
                pkcs12_password=self._senha.encode("utf-8") if self._senha else b"",
            ),
        )
        return sessao

    def requisitar(
        self, nsu: int, *, lote: bool = True, capturar_tls: bool = False
    ) -> tuple[int, bytes, int | None]:
        """Uma chamada `GET /DFe/{NSU}`: devolve `(http_status, corpo, retry_after)` crus.

        Levanta `ErroADN(CONEXAO)` (com `.falha`) se a conexão falhar antes de haver resposta
        HTTP. Com `capturar_tls`, guarda em `ultima_info_tls` a versão e o cifrador negociados.
        """
        params = {"cnpjConsulta": self._cnpj, "lote": "true" if lote else "false"}
        sessao = self._sessao_http if self._sessao_http is not None else self._criar_sessao_mtls()
        self.ultima_info_tls = None
        extra = {"stream": True} if capturar_tls else {}
        try:
            resp = sessao.get(
                self.url_nsu(nsu),
                params=params,
                headers={"Accept": "application/json, text/plain, */*"},
                timeout=self._timeout,
                **extra,
            )
        except requests.RequestException as exc:
            falha = analisar_falha_conexao(exc)
            raise ErroADN(mensagem_falha_conexao(falha), categoria="CONEXAO", falha=falha) from exc
        except ValueError as exc:  # ex.: certificado vencido recusado pelo adaptador
            raise ErroADN(
                "O certificado A1 não pôde ser usado na conexão (vencido ou inválido).",
                categoria="ACESSO",
            ) from exc

        if capturar_tls:
            self.ultima_info_tls = _info_tls(resp)
        status = int(getattr(resp, "status_code", 0))
        corpo = getattr(resp, "content", b"") or str(getattr(resp, "text", "")).encode("utf-8")
        cabecalhos = getattr(resp, "headers", None) or {}
        return status, corpo, _segundos_retry_after(cabecalhos.get("Retry-After"))

    def consultar_nsu(self, nsu: int, *, lote: bool = True) -> RespostaADN:
        """Busca documentos com NSU maior que `nsu` (exclusivo)."""
        status, corpo, retry_apos = self.requisitar(nsu, lote=lote)

        if status == 429:
            raise ErroADN(
                "O ADN pediu para reduzir o ritmo de consultas (limite de uso).",
                categoria="LIMITE",
                http_status=status,
                retry_apos_segundos=retry_apos or ESPERA_PADRAO_LIMITE_SEGUNDOS,
            )
        if status >= 500:
            raise ErroADN(
                f"O ADN está indisponível no momento (HTTP {status}).",
                categoria="INDISPONIVEL",
                http_status=status,
                retry_apos_segundos=retry_apos or ESPERA_PADRAO_INDISPONIVEL_SEGUNDOS,
            )
        if status in (401, 403):
            raise ErroADN(
                f"O ADN negou o acesso (HTTP {status}). Confira o certificado A1 cadastrado.",
                categoria="ACESSO",
                http_status=status,
            )
        if status == 404:
            if not eh_resposta_fila_zerada(corpo):
                raise ErroADN(
                    mensagem_endereco_nao_encontrado(self.url_nsu(nsu)),
                    categoria="ENDERECO",
                    http_status=404,
                )
            resposta = parse_resposta_adn(404, corpo)
            resposta.status_processamento = "NENHUM_DOCUMENTO_LOCALIZADO"
            return resposta
        if status in (200, 400):
            return parse_resposta_adn(status, corpo)
        raise ErroADN(
            f"O ADN respondeu com HTTP {status}, que não consta na documentação.",
            categoria="RESPOSTA_INVALIDA",
            http_status=status,
        )


def eh_resposta_fila_zerada(corpo: bytes | str) -> bool:
    """`True` só se o 404 trouxer JSON válido com NENHUM_DOCUMENTO_LOCALIZADO ou E2220/E2230."""
    texto_corpo = corpo.decode("utf-8", errors="replace") if isinstance(corpo, bytes) else corpo
    try:
        dados = json.loads(texto_corpo)
    except ValueError:
        return False
    if not isinstance(dados, dict):
        return False
    if str(_chave_ci(dados, "StatusProcessamento") or "").upper() == "NENHUM_DOCUMENTO_LOCALIZADO":
        return True
    return any(
        m.codigo.strip().upper() in CODIGOS_FILA_ZERADA
        for m in _mensagens(_chave_ci(dados, "Erros"))
    )


def mensagem_endereco_nao_encontrado(url: str) -> str:
    return (
        "O endereço do ADN não foi encontrado (HTTP 404 sem a resposta esperada). "
        f"A URL base pode estar incorreta: confira {VARIAVEL_URL_BASE}. URL usada: {url}"
    )


def resolver_url_base(ambiente: Literal[1, 2], explicita: str | None = None) -> str:
    """URL base: argumento (`--url-base`), `NFE_ADN_URL_BASE` ou a constante do ambiente."""
    for candidata in (explicita, os.environ.get(VARIAVEL_URL_BASE)):
        if candidata and candidata.strip():
            limpa = candidata.strip().rstrip("/")
            if not limpa.lower().startswith("https://") or len(limpa) <= len("https://"):
                raise ValueError(
                    f"A URL base do ADN deve começar com https:// (conferir {VARIAVEL_URL_BASE} "
                    "ou --url-base)."
                )
            return limpa
    return URL_ADN_PRODUCAO if ambiente == 1 else URL_ADN_HOMOLOGACAO


def resolver_cnpj_consulta(explicito: str | None, documento_certificado: str) -> str:
    """CNPJ de 14 posições: argumento, `NFE_CNPJ_CONSULTA` ou o do certificado (nessa ordem)."""
    for candidato in (
        explicito,
        os.environ.get("NFE_CNPJ_CONSULTA", ""),
        documento_certificado,
    ):
        limpo = limpar_documento(candidato or "")
        if limpo:
            if len(limpo) != 14:
                raise ValueError(
                    "O ADN exige o CNPJ de consulta completo, com 14 posições "
                    "(conferir NFE_CNPJ_CONSULTA)."
                )
            return limpo
    raise ValueError(
        "Não foi possível determinar o CNPJ de consulta: informe NFE_CNPJ_CONSULTA ou use um "
        "certificado A1 com CNPJ identificável."
    )
