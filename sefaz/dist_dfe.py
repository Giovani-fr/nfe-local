"""Cliente SOAP 1.2 do WebService `NFeDistribuicaoDFe` (Ambiente Nacional da NF-e)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

import requests
from pydantic import BaseModel, ConfigDict, Field
from requests_pkcs12 import Pkcs12Adapter

from models.normalizadores import limpar_documento, para_data_hora
from parsers.xml_seguro import ErroParseXML, parse_xml_seguro, primeiro, texto, todos
from sefaz.adn import FalhaConexao, analisar_falha_conexao
from sefaz.cert_a1 import (
    ErroCertificadoA1,
    MetadadosCertificado,
    extrair_metadados_certificado,
)

URL_DIST_DFE_HOMOLOGACAO = (
    "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
)
URL_DIST_DFE_PRODUCAO = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"

SOAP_ACTION_DIST_DFE = (
    "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse"
)
CONTENT_TYPE_SOAP_DIST_DFE = f'application/soap+xml; charset=utf-8; action="{SOAP_ACTION_DIST_DFE}"'


class ErroComunicacaoSEFAZ(Exception):
    """Exceção levantada em falhas de rede, HTTP, SOAP Fault ou resposta inválida da SEFAZ."""


class ErroConexaoSEFAZ(ErroComunicacaoSEFAZ):
    """Falha de CONEXÃO (tempo esgotado, reset, nome, TLS): a única que justifica repetir."""

    def __init__(self, mensagem: str, *, falha: FalhaConexao, tentativas: int = 1) -> None:
        super().__init__(mensagem)
        self.falha = falha
        self.tentativas = tentativas


TIMEOUT_PADRAO_SEGUNDOS = 90.0
TIMEOUT_MAXIMO_SEGUNDOS = 180.0
TENTATIVAS_PADRAO = 3
TENTATIVAS_MAXIMO = 5
# Espera crescente (segundos) antes da 2ª, 3ª, 4ª... tentativa; a última se repete.
ESPERAS_RETENTATIVA_PADRAO: tuple[float, ...] = (5.0, 15.0, 30.0)
VARIAVEL_TIMEOUT = "NFE_SEFAZ_TIMEOUT"
VARIAVEL_TENTATIVAS = "NFE_SEFAZ_TENTATIVAS"


def resolver_timeout_sefaz(explicito: float | str | None = None) -> float:
    """Tempo limite por chamada (segundos): argumento, `NFE_SEFAZ_TIMEOUT` ou 90; de 1 a 180."""
    bruto: float | str | None = explicito
    if bruto is None:
        bruto = os.environ.get(VARIAVEL_TIMEOUT, "").strip() or None
    if bruto is None:
        return TIMEOUT_PADRAO_SEGUNDOS
    try:
        valor = float(bruto)
    except (TypeError, ValueError):
        valor = 0.0
    if not 1.0 <= valor <= TIMEOUT_MAXIMO_SEGUNDOS:
        raise ValueError(
            f"O tempo limite da SEFAZ deve ser um número de 1 a {TIMEOUT_MAXIMO_SEGUNDOS:g} "
            f"segundos (--timeout ou {VARIAVEL_TIMEOUT})."
        )
    return valor


def resolver_tentativas_sefaz(explicita: int | str | None = None) -> int:
    """Tentativas por consulta (1 a 5): argumento, `NFE_SEFAZ_TENTATIVAS` ou 3."""
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


def mensagem_conexao_sefaz(falha: FalhaConexao, tentativas: int) -> str:
    """Texto amigável (sem URL, CNPJ, caminho do certificado, senha nem XML)."""
    vezes = "vez" if tentativas == 1 else "vezes"
    if falha.tempo_esgotado:
        return (
            f"A SEFAZ demorou demais para responder (tentamos {tentativas} {vezes}). "
            "O serviço de NF-e costuma ficar lento; tente de novo em alguns minutos, "
            "de preferência fora do horário comercial."
        )
    return (
        f"Falha de conexão com a SEFAZ (etapa: {falha.etapa}; tentamos {tentativas} {vezes}). "
        "Falhas intermitentes são comuns; tente de novo em alguns minutos. "
        f"Se continuar: {falha.orientacao}"
    )


class RepetidorConsultaSEFAZ:
    """Repete a MESMA consulta só em falha de conexão; nunca em resposta SOAP nem certificado."""

    def __init__(
        self,
        consultar_fn: Callable[[str], RespostaDistDFe],
        *,
        tentativas: int = TENTATIVAS_PADRAO,
        esperas: Sequence[float] = ESPERAS_RETENTATIVA_PADRAO,
        dormir_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._fn = consultar_fn
        self._tentativas = max(1, tentativas)
        self._esperas = tuple(esperas)
        self._dormir = dormir_fn
        self.tentativas_usadas = 0
        self.falhas: list[str] = []
        self._pagina = 0

    def __call__(self, ult_nsu: str) -> RespostaDistDFe:
        self._pagina += 1
        for k in range(self._tentativas):
            if k > 0 and self._esperas:
                (self._dormir or time.sleep)(self._esperas[min(k - 1, len(self._esperas) - 1)])
            self.tentativas_usadas += 1
            try:
                return self._fn(ult_nsu)
            except ErroConexaoSEFAZ as exc:
                # Só etapa e classe da exceção (nunca URL, CNPJ, caminho, senha nem XML).
                self.falhas.append(
                    f"pág. {self._pagina}, tentativa {k + 1}: {exc.falha.etapa} "
                    f"[{exc.falha.excecao_raiz}]"
                )
                if k == self._tentativas - 1:
                    raise ErroConexaoSEFAZ(
                        mensagem_conexao_sefaz(exc.falha, self._tentativas),
                        falha=exc.falha,
                        tentativas=self._tentativas,
                    ) from exc
        raise AssertionError("inalcançável")  # pragma: no cover


class ItemDocZip(BaseModel):
    """Representa um bloco `<docZip NSU="..." schema="...">` retornado pela SEFAZ."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    nsu: str
    schema_xml: str
    conteudo_base64: str


class RespostaDistDFe(BaseModel):
    """Estrutura parseada do retorno `<retDistDFeInt>` do WebService `NFeDistribuicaoDFe`."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    ambiente: Literal[1, 2] = 2
    ver_aplic: str = ""
    cstat: str
    xmotivo: str
    dh_resp: datetime | None = None
    ult_nsu: str = "000000000000000"
    max_nsu: str = "000000000000000"
    lote_doc_zip: list[ItemDocZip] = Field(default_factory=list)


def formatar_nsu(nsu: int | str) -> str:
    """Formata um NSU estritamente com 15 dígitos numéricos preenchidos com zeros à esquerda."""
    if isinstance(nsu, int):
        if nsu < 0:
            raise ValueError(f"NSU não pode ser negativo: {nsu}")
        nsu_str = str(nsu)
    else:
        nsu_str = str(nsu).strip()
        if not nsu_str.isdigit():
            raise ValueError(f"NSU deve conter apenas dígitos numéricos: {nsu!r}")

    if len(nsu_str) > 15:
        raise ValueError(f"NSU excede o limite de 15 dígitos do tipo TNSU: {nsu!r}")

    return nsu_str.zfill(15)


def resolver_ambiente_sefaz(valor: int | str | None = None) -> Literal[1, 2]:
    """Converte entrada (`1`/`2` ou `'producao'`/`'homologacao'`) em `1` ou `2` (padrão `2`)."""
    if valor in (1, "1"):
        return 1
    if valor in (2, "2"):
        return 2
    bruto = str(valor if valor is not None else os.environ.get("NFE_AMBIENTE", "homologacao"))
    limpo = bruto.strip().lower()
    if limpo in ("1", "producao", "produção", "prod"):
        return 1
    return 2


def montar_envelope_dist_dfe(
    documento: str,
    ult_nsu: int | str = 0,
    uf_autor: str = "35",
    ambiente: Literal[1, 2] = 2,
) -> bytes:
    """Monta o envelope SOAP 1.2 `nfeDistDFeInteresse` (`distDFeInt` v1.01) por `ultNSU`."""
    doc_limpo = limpar_documento(documento)
    if len(doc_limpo) == 14:
        tag_doc = f"<CNPJ>{doc_limpo}</CNPJ>"
    elif len(doc_limpo) == 11 and doc_limpo.isdigit():
        tag_doc = f"<CPF>{doc_limpo}</CPF>"
    else:
        raise ValueError(
            "Documento inválido para consulta DistDFe "
            f"(esperado CNPJ de 14 posições ou CPF de 11): {documento!r}"
        )

    uf_limpa = str(uf_autor).strip()
    if not uf_limpa.isdigit() or len(uf_limpa) != 2:
        raise ValueError(
            f"Código IBGE de UF inválido ('cUFAutor' deve ter 2 dígitos): {uf_autor!r}"
        )

    tp_amb = 1 if ambiente == 1 else 2
    nsu_15 = formatar_nsu(ult_nsu)

    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        "<soap12:Body>"
        '<nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">'
        "<nfeDadosMsg>"
        '<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
        f"<tpAmb>{tp_amb}</tpAmb>"
        f"<cUFAutor>{uf_limpa}</cUFAutor>"
        f"{tag_doc}"
        f"<distNSU><ultNSU>{nsu_15}</ultNSU></distNSU>"
        "</distDFeInt>"
        "</nfeDadosMsg>"
        "</nfeDistDFeInteresse>"
        "</soap12:Body>"
        "</soap12:Envelope>"
    )
    return envelope.encode("utf-8")


def parse_resposta_dist_dfe(xml_soap: bytes | str) -> RespostaDistDFe:
    """Analisa o envelope SOAP de resposta da SEFAZ extraindo `<retDistDFeInt>` e `<docZip>`."""
    try:
        raiz = parse_xml_seguro(xml_soap)
    except ErroParseXML as exc:
        raise ErroComunicacaoSEFAZ(f"Resposta XML inválida da SEFAZ: {exc}") from exc

    fault = primeiro(raiz, "Fault")
    if fault is not None:
        motivo_fault = (
            texto(fault, "Text")
            or texto(fault, "faultstring")
            or texto(fault, "Reason")
            or "SOAP Fault sem descrição detalhada"
        )
        codigo_fault = texto(fault, "Value") or texto(fault, "faultcode")
        detalhe = f"{codigo_fault}: {motivo_fault}" if codigo_fault else motivo_fault
        raise ErroComunicacaoSEFAZ(f"SEFAZ retornou SOAP Fault: {detalhe}")

    ret = primeiro(raiz, "retDistDFeInt")
    if ret is None:
        raise ErroComunicacaoSEFAZ(
            "Elemento '<retDistDFeInt>' não encontrado no envelope SOAP retornado pela SEFAZ."
        )

    tp_amb_str = texto(ret, "tpAmb")
    ambiente: Literal[1, 2] = 1 if tp_amb_str == "1" else 2
    ver_aplic = texto(ret, "verAplic")
    cstat = texto(ret, "cStat")
    xmotivo = texto(ret, "xMotivo")
    dh_resp = para_data_hora(texto(ret, "dhResp"))

    if not cstat:
        raise ErroComunicacaoSEFAZ(
            "Resposta '<retDistDFeInt>' não contém o campo obrigatório '<cStat>'."
        )

    ult_nsu_bruto = texto(ret, "ultNSU") or "0"
    max_nsu_bruto = texto(ret, "maxNSU") or "0"
    ult_nsu = formatar_nsu(ult_nsu_bruto) if ult_nsu_bruto.isdigit() else "000000000000000"
    max_nsu = formatar_nsu(max_nsu_bruto) if max_nsu_bruto.isdigit() else "000000000000000"

    itens_doc_zip: list[ItemDocZip] = []
    lote_el = primeiro(ret, "loteDistDFeInt")
    if lote_el is not None:
        for nodo_zip in todos(lote_el, "docZip"):
            nsu_attr = (nodo_zip.attrib.get("NSU") or nodo_zip.attrib.get("nsu") or "0").strip()
            nsu_fmt = formatar_nsu(nsu_attr) if nsu_attr.isdigit() else nsu_attr
            schema_attr = (nodo_zip.attrib.get("schema") or "").strip()
            conteudo_b64 = "".join((nodo_zip.text or "").split())
            if conteudo_b64:
                itens_doc_zip.append(
                    ItemDocZip(
                        nsu=nsu_fmt,
                        schema_xml=schema_attr,
                        conteudo_base64=conteudo_b64,
                    )
                )

    return RespostaDistDFe(
        ambiente=ambiente,
        ver_aplic=ver_aplic,
        cstat=cstat,
        xmotivo=xmotivo,
        dh_resp=dh_resp,
        ult_nsu=ult_nsu,
        max_nsu=max_nsu,
        lote_doc_zip=itens_doc_zip,
    )


class ClienteNFeDistribuicaoDFe:
    """Cliente SOAP 1.2 com mTLS (PKCS#12 via `requests-pkcs12`; usa `.pem` temporário)."""

    def __init__(
        self,
        *,
        pfx_bytes: bytes,
        senha: str,
        uf_autor: str | None = None,
        ambiente: Literal[1, 2] | str | None = None,
        sessao_http: Any | None = None,
        timeout_segundos: float | None = None,
    ) -> None:
        self._pfx_bytes = pfx_bytes
        self._senha = senha
        self._metadados = extrair_metadados_certificado(pfx_bytes, senha)
        self._uf_autor = (uf_autor or os.environ.get("NFE_UF_AUTOR", "").strip() or "35").strip()
        self._ambiente: Literal[1, 2] = resolver_ambiente_sefaz(ambiente)
        self._sessao_http = sessao_http
        self._timeout_segundos = (
            resolver_timeout_sefaz() if timeout_segundos is None else float(timeout_segundos)
        )

    @property
    def metadados_certificado(self) -> MetadadosCertificado:
        """Retorna os metadados extraídos do certificado A1 configurado."""
        return self._metadados

    @property
    def ambiente(self) -> Literal[1, 2]:
        """Retorna o código do ambiente SEFAZ (`1` Produção, `2` Homologação)."""
        return self._ambiente

    @property
    def url_servico(self) -> str:
        """Retorna a URL oficial do WebService `NFeDistribuicaoDFe` para o ambiente ativo."""
        return URL_DIST_DFE_PRODUCAO if self._ambiente == 1 else URL_DIST_DFE_HOMOLOGACAO

    def _criar_sessao_mtls(self) -> requests.Session:
        sessao = requests.Session()
        adapter = Pkcs12Adapter(
            pkcs12_data=self._pfx_bytes,
            pkcs12_password=self._senha.encode("utf-8") if self._senha else b"",
        )
        sessao.mount("https://", adapter)
        return sessao

    @property
    def documento_consulta(self) -> str:
        """CNPJ que será consultado: `NFE_CNPJ_CONSULTA` ou o do certificado A1."""
        return (
            limpar_documento(os.environ.get("NFE_CNPJ_CONSULTA", "")) or self._metadados.documento
        )

    def consultar_dist_nsu(
        self,
        ult_nsu: int | str = 0,
        documento: str | None = None,
    ) -> RespostaDistDFe:
        """Executa a chamada `nfeDistDFeInteresse` buscando documentos a partir de `ult_nsu`."""
        doc_consulta = (
            limpar_documento(documento)
            or limpar_documento(os.environ.get("NFE_CNPJ_CONSULTA", ""))
            or self._metadados.documento
        )
        if not doc_consulta:
            raise ValueError(
                "Não foi possível determinar o CNPJ/CPF para consulta DistDFe: "
                "nenhum documento informado e o certificado A1 não contém CNPJ identificável."
            )

        envelope_bytes = montar_envelope_dist_dfe(
            documento=doc_consulta,
            ult_nsu=ult_nsu,
            uf_autor=self._uf_autor,
            ambiente=self._ambiente,
        )
        headers = {
            "Content-Type": CONTENT_TYPE_SOAP_DIST_DFE,
        }

        try:
            sessao = (
                self._sessao_http if self._sessao_http is not None else self._criar_sessao_mtls()
            )
            resp = sessao.post(
                self.url_servico,
                data=envelope_bytes,
                headers=headers,
                timeout=self._timeout_segundos,
            )
        except requests.RequestException as exc:
            falha = analisar_falha_conexao(exc, servico="a SEFAZ")
            raise ErroConexaoSEFAZ(mensagem_conexao_sefaz(falha, 1), falha=falha) from exc
        except ValueError as exc:  # certificado vencido/inválido recusado pelo adaptador
            raise ErroCertificadoA1(
                "O certificado A1 não pôde ser usado na conexão (vencido ou inválido)."
            ) from exc

        conteudo_resposta = getattr(resp, "content", b"") or str(getattr(resp, "text", "")).encode(
            "utf-8"
        )

        if getattr(resp, "status_code", 200) >= 400:
            # Se o servidor retornou um SOAP Fault em XML mesmo com HTTP 500, extrai o Fault
            if b"Fault" in conteudo_resposta:
                return parse_resposta_dist_dfe(conteudo_resposta)
            raise ErroComunicacaoSEFAZ(
                f"WebService SEFAZ retornou HTTP {resp.status_code}: "
                f"{conteudo_resposta.decode('utf-8', errors='replace')[:300]}"
            )

        return parse_resposta_dist_dfe(conteudo_resposta)
