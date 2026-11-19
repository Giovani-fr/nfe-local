"""Agregador de resumo gerencial de lote fiscal (emitente, imposto e período)."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from models.fiscal import ImpostoTotal, ResultadoAnalise
from models.normalizadores import DECIMAL_ZERO, limpar_chave_acesso

_TIPOS_FINANCEIROS = {"NFE", "NFCE", "NFSE", "RESUMO"}

# Agrupa o resumo `resNFe` com a NF-e de produto nos totais por tipo.
GRUPO_TIPO = {"NFE": "NFE", "RESUMO": "NFE", "NFCE": "NFCE", "NFSE": "NFSE"}
PAPEIS_QUEBRA = ("EMITIDA", "RECEBIDA", "INTERMEDIARIA", "SEM_PAPEL")
CRITERIO_SOMAS = (
    "As somas de valor consideram NF-e (produto), NFS-e (serviço) e NFC-e (consumidor) "
    "autorizadas, sem repetir a mesma chave. Notas CANCELADAS e eventos ficam fora da soma e "
    "aparecem à parte, com a contagem. Emitida/Recebida/Intermediária compara o CNPJ de "
    "consulta (o do certificado) com o emitente/prestador e o destinatário/tomador; sem esse "
    'CNPJ de referência a nota entra em "Sem papel". O valor de um resumo (resNFe) conta na NF-e.'
)


def _somar_impostos(destino: ImpostoTotal, origem: ImpostoTotal) -> None:
    """Acumula in-place os campos de `ImpostoTotal`."""
    destino.icms_bc += origem.icms_bc
    destino.icms += origem.icms
    destino.icms_st_bc += origem.icms_st_bc
    destino.icms_st += origem.icms_st
    destino.ipi += origem.ipi
    destino.pis += origem.pis
    destino.cofins += origem.cofins
    destino.iss += origem.iss
    destino.retencoes_federais += origem.retencoes_federais
    destino.ibs_cbs += origem.ibs_cbs
    destino.tributos_aproximados += origem.tributos_aproximados


class ResumoEmitente(BaseModel):
    """Totais consolidados por emitente/prestador dentro do lote."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    documento: str = ""
    razao_social: str = ""
    uf: str | None = None
    quantidade_notas: int = 0
    valor_produtos_servicos: Decimal = DECIMAL_ZERO
    valor_liquido: Decimal = DECIMAL_ZERO
    valor_total_notas: Decimal = DECIMAL_ZERO
    impostos: ImpostoTotal = Field(default_factory=ImpostoTotal)


class ResumoPeriodo(BaseModel):
    """Totais consolidados por período (mensal `YYYY-MM` ou diário `YYYY-MM-DD`)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    periodo: str = "SEM_DATA"
    quantidade_notas: int = 0
    valor_produtos_servicos: Decimal = DECIMAL_ZERO
    valor_liquido: Decimal = DECIMAL_ZERO
    valor_total_notas: Decimal = DECIMAL_ZERO
    impostos: ImpostoTotal = Field(default_factory=ImpostoTotal)


class ContagemStatusLote(BaseModel):
    """Contadores de validação e classificação documental do lote."""

    model_config = ConfigDict(extra="ignore")

    total_arquivos: int = 0
    validos: int = 0
    com_erros: int = 0
    com_avisos: int = 0
    erros_fatais_xml: int = 0
    duplicados_ignorados_nos_totais: int = 0
    canceladas_fora_dos_totais: int = 0
    por_tipo: dict[str, int] = Field(default_factory=dict)


class ResumoLote(BaseModel):
    """Resumo executivo e contábil de um lote de documentos fiscais."""

    model_config = ConfigDict(extra="ignore")

    contagem: ContagemStatusLote = Field(default_factory=ContagemStatusLote)
    valor_produtos_servicos_total: Decimal = DECIMAL_ZERO
    valor_frete_total: Decimal = DECIMAL_ZERO
    valor_seguro_total: Decimal = DECIMAL_ZERO
    valor_desconto_total: Decimal = DECIMAL_ZERO
    valor_outras_despesas_total: Decimal = DECIMAL_ZERO
    valor_liquido_total: Decimal = DECIMAL_ZERO
    valor_total_notas: Decimal = DECIMAL_ZERO
    valor_canceladas_fora_dos_totais: Decimal = DECIMAL_ZERO
    impostos_totais: ImpostoTotal = Field(default_factory=ImpostoTotal)
    por_emitente: list[ResumoEmitente] = Field(default_factory=list)
    por_mes: list[ResumoPeriodo] = Field(default_factory=list)
    por_dia: list[ResumoPeriodo] = Field(default_factory=list)


class AcumuladorResumoLote:
    """Acumulador incremental O(1) em árvores DOM para lotes grandes (1.000+ XMLs)."""

    def __init__(self) -> None:
        self._contagem = ContagemStatusLote()
        self._valor_produtos_servicos = DECIMAL_ZERO
        self._valor_frete = DECIMAL_ZERO
        self._valor_seguro = DECIMAL_ZERO
        self._valor_desconto = DECIMAL_ZERO
        self._valor_outras_despesas = DECIMAL_ZERO
        self._valor_liquido = DECIMAL_ZERO
        self._valor_total_notas = DECIMAL_ZERO
        self._valor_canceladas = DECIMAL_ZERO
        self._impostos_totais = ImpostoTotal()

        self._emitentes: dict[str, ResumoEmitente] = {}
        self._meses: dict[str, ResumoPeriodo] = {}
        self._dias: dict[str, ResumoPeriodo] = {}
        self._chaves_vistas: set[str] = set()

    def adicionar(self, resultado: ResultadoAnalise) -> None:
        """Incorpora um único `ResultadoAnalise` nas estatísticas e somatórios do lote."""
        self._contagem.total_arquivos += 1

        tem_erro = not resultado.valido or any(a.severidade == "ERRO" for a in resultado.alertas)
        tem_aviso = any(a.severidade == "AVISO" for a in resultado.alertas)

        if tem_erro:
            self._contagem.com_erros += 1
        else:
            self._contagem.validos += 1

        if tem_aviso:
            self._contagem.com_avisos += 1

        if resultado.erro_fatal is not None or resultado.documento is None:
            self._contagem.erros_fatais_xml += 1
            self._contagem.por_tipo["ERRO_XML"] = self._contagem.por_tipo.get("ERRO_XML", 0) + 1
            return

        doc = resultado.documento
        self._contagem.por_tipo[doc.tipo] = self._contagem.por_tipo.get(doc.tipo, 0) + 1

        if doc.tipo not in _TIPOS_FINANCEIROS:
            return

        chave = limpar_chave_acesso(doc.chave_acesso)
        if chave:
            if chave in self._chaves_vistas:
                self._contagem.duplicados_ignorados_nos_totais += 1
                return
            self._chaves_vistas.add(chave)

        tot = doc.totais
        v_prod = tot.valor_produtos_servicos
        v_tot = tot.valor_total_nota

        if doc.situacao == "CANCELADA":
            # Nota cancelada não entra em nenhuma soma de valor: fica à parte, só contada.
            self._contagem.canceladas_fora_dos_totais += 1
            self._valor_canceladas += v_tot
            return
        v_liq = tot.valor_liquido if tot.valor_liquido != DECIMAL_ZERO else v_tot

        self._valor_produtos_servicos += v_prod
        self._valor_frete += tot.valor_frete
        self._valor_seguro += tot.valor_seguro
        self._valor_desconto += tot.valor_desconto
        self._valor_outras_despesas += tot.valor_outras_despesas
        self._valor_liquido += v_liq
        self._valor_total_notas += v_tot
        _somar_impostos(self._impostos_totais, tot.impostos)

        chave_emit = doc.emitente.documento or doc.emitente.razao_social or "NAO_IDENTIFICADO"
        res_emit = self._emitentes.get(chave_emit)
        if res_emit is None:
            res_emit = ResumoEmitente(
                documento=doc.emitente.documento,
                razao_social=doc.emitente.razao_social,
                uf=doc.emitente.uf,
            )
            self._emitentes[chave_emit] = res_emit
        elif not res_emit.razao_social and doc.emitente.razao_social:
            res_emit.razao_social = doc.emitente.razao_social

        res_emit.quantidade_notas += 1
        res_emit.valor_produtos_servicos += v_prod
        res_emit.valor_liquido += v_liq
        res_emit.valor_total_notas += v_tot
        _somar_impostos(res_emit.impostos, tot.impostos)

        data_ref = doc.data_emissao.date() if doc.data_emissao else doc.competencia
        str_mes = data_ref.strftime("%Y-%m") if data_ref else "SEM_DATA"
        str_dia = data_ref.strftime("%Y-%m-%d") if data_ref else "SEM_DATA"

        for mapa, chave_per in ((self._meses, str_mes), (self._dias, str_dia)):
            res_per = mapa.get(chave_per)
            if res_per is None:
                res_per = ResumoPeriodo(periodo=chave_per)
                mapa[chave_per] = res_per
            res_per.quantidade_notas += 1
            res_per.valor_produtos_servicos += v_prod
            res_per.valor_liquido += v_liq
            res_per.valor_total_notas += v_tot
            _somar_impostos(res_per.impostos, tot.impostos)

    def consolidar(self) -> ResumoLote:
        """Retorna o `ResumoLote` consolidado e ordenado."""
        emitentes_ordenados = sorted(
            self._emitentes.values(),
            key=lambda e: (-e.valor_total_notas, e.documento),
        )
        meses_ordenados = sorted(self._meses.values(), key=lambda p: p.periodo)
        dias_ordenados = sorted(self._dias.values(), key=lambda p: p.periodo)

        return ResumoLote(
            contagem=self._contagem.model_copy(deep=True),
            valor_produtos_servicos_total=self._valor_produtos_servicos,
            valor_frete_total=self._valor_frete,
            valor_seguro_total=self._valor_seguro,
            valor_desconto_total=self._valor_desconto,
            valor_outras_despesas_total=self._valor_outras_despesas,
            valor_liquido_total=self._valor_liquido,
            valor_total_notas=self._valor_total_notas,
            valor_canceladas_fora_dos_totais=self._valor_canceladas,
            impostos_totais=self._impostos_totais.model_copy(deep=True),
            por_emitente=emitentes_ordenados,
            por_mes=meses_ordenados,
            por_dia=dias_ordenados,
        )


def gerar_resumo_lote(resultados: Iterable[ResultadoAnalise]) -> ResumoLote:
    """Consome qualquer iterável/stream de `ResultadoAnalise` e retorna o `ResumoLote`."""
    acumulador = AcumuladorResumoLote()
    for resultado in resultados:
        acumulador.adicionar(resultado)
    return acumulador.consolidar()


# --- Quebras por tipo e por papel (para os cartões e o painel de resumo) -----------------------


class SomaPapel(BaseModel):
    """Quantidade e valor de um recorte."""

    model_config = ConfigDict(extra="ignore")

    quantidade: int = 0
    valor: Decimal = DECIMAL_ZERO


class QuebraTipo(BaseModel):
    """Soma de um tipo (NFE/NFSE/NFCE) e sua divisão por papel."""

    model_config = ConfigDict(extra="ignore")

    quantidade: int = 0
    valor: Decimal = DECIMAL_ZERO
    papeis: dict[str, SomaPapel] = Field(default_factory=dict)


class ErroXmlDetalhe(BaseModel):
    """Um XML ilegível (ERRO_XML) e o motivo."""

    model_config = ConfigDict(extra="ignore")

    arquivo: str
    codigo: str
    motivo: str


class QuebrasResumo(BaseModel):
    """Totais separados por tipo e por papel, sem canceladas e sem eventos."""

    model_config = ConfigDict(extra="ignore")

    por_tipo: dict[str, QuebraTipo] = Field(default_factory=dict)
    por_papel: dict[str, SomaPapel] = Field(default_factory=dict)
    quantidade_total: int = 0
    valor_total: Decimal = DECIMAL_ZERO
    canceladas: SomaPapel = Field(default_factory=SomaPapel)
    canceladas_por_tipo: dict[str, int] = Field(default_factory=dict)
    eventos: int = 0
    resumos_nfe: int = 0
    duplicadas_ignoradas: int = 0
    erros_xml: list[ErroXmlDetalhe] = Field(default_factory=list)
    criterio: str = CRITERIO_SOMAS


class AcumuladorQuebras:
    """Acumula os totais por tipo e papel (sem canceladas nem eventos)."""

    def __init__(self) -> None:
        self._q = QuebrasResumo(
            por_tipo={
                t: QuebraTipo(papeis={p: SomaPapel() for p in PAPEIS_QUEBRA})
                for t in ("NFE", "NFSE", "NFCE")
            },
            por_papel={p: SomaPapel() for p in PAPEIS_QUEBRA},
        )
        self._chaves: set[str] = set()

    def adicionar_nota(
        self,
        tipo: str,
        papel: str | None,
        valor: Decimal,
        cancelada: bool,
        chave: str = "",
    ) -> None:
        grupo = GRUPO_TIPO.get(tipo)
        if grupo is None:
            return
        chave_limpa = limpar_chave_acesso(chave) if chave else ""
        if chave_limpa:
            if chave_limpa in self._chaves:
                self._q.duplicadas_ignoradas += 1
                return
            self._chaves.add(chave_limpa)
        if cancelada:
            self._q.canceladas.quantidade += 1
            self._q.canceladas.valor += valor
            self._q.canceladas_por_tipo[grupo] = self._q.canceladas_por_tipo.get(grupo, 0) + 1
            return
        if tipo == "RESUMO":
            self._q.resumos_nfe += 1
        chave_papel = papel if papel in PAPEIS_QUEBRA else "SEM_PAPEL"
        bloco = self._q.por_tipo[grupo]
        bloco.quantidade += 1
        bloco.valor += valor
        bloco.papeis[chave_papel].quantidade += 1
        bloco.papeis[chave_papel].valor += valor
        self._q.por_papel[chave_papel].quantidade += 1
        self._q.por_papel[chave_papel].valor += valor
        self._q.quantidade_total += 1
        self._q.valor_total += valor

    def adicionar_evento(self) -> None:
        self._q.eventos += 1

    def adicionar_erro_xml(self, arquivo: str, codigo: str, motivo: str) -> None:
        self._q.erros_xml.append(ErroXmlDetalhe(arquivo=arquivo, codigo=codigo, motivo=motivo))

    def consolidar(self) -> QuebrasResumo:
        return self._q


def gerar_quebras(
    resultados: Iterable[ResultadoAnalise], documento_alvo: str | None = None
) -> QuebrasResumo:
    """Totais por tipo e papel de um lote/escopo; ERRO_XML e eventos ficam à parte."""
    from sefaz.adn import calcular_papel_visao  # import tardio: evita ciclo de pacotes

    acc = AcumuladorQuebras()
    for res in resultados:
        doc = res.documento
        if res.erro_fatal is not None or doc is None:
            codigo = res.alertas[0].codigo if res.alertas else "XML_INVALIDO"
            acc.adicionar_erro_xml(res.arquivo_origem, codigo, res.erro_fatal or "XML inválido")
            continue
        if doc.tipo == "EVENTO":
            acc.adicionar_evento()
            continue
        papel = res.papel or calcular_papel_visao(
            documento_alvo,
            doc.emitente.documento,
            doc.destinatario.documento if doc.destinatario else "",
        )
        acc.adicionar_nota(
            doc.tipo,
            papel,
            doc.totais.valor_total_nota,
            doc.situacao == "CANCELADA",
            doc.chave_acesso,
        )
    return acc.consolidar()
