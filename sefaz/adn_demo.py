"""NFS-e 100% sintéticas para o modo `--demo` do ADN (sem rede, sem certificado).

Todos os CNPJs e nomes são fictícios.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sefaz.adn import ItemADN, RespostaADN
from sefaz.descompactador import compactar_xml_para_doc_zip_b64

CNPJ_DEMO = "11222333000181"
_ALVO = ("EMPRESA EXEMPLO LTDA", CNPJ_DEMO)
_GAMA = ("SERVICOS GAMA EXEMPLO SA", "33444555000181")
_DELTA = ("CONSULTORIA DELTA EXEMPLO LTDA", "55666777000181")

_MODELO_NFSE = """<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00">
  <infNFSe Id="NFS{chave}" versao="1.00">
    <xLocEmi>SAO PAULO</xLocEmi>
    <nNFSe>{numero}</nNFSe>
    <ambGer>2</ambGer>
    <cStat>100</cStat>
    <dhProc>2026-10-{dia}T10:15:00-03:00</dhProc>
    <emit>
      <CNPJ>{prest_doc}</CNPJ>
      <xNome>{prest_nome}</xNome>
      <enderNac><xLgr>RUA EXEMPLO</xLgr><nro>1</nro><cMun>3550308</cMun><UF>SP</UF></enderNac>
    </emit>
    <valores><vBC>{valor}</vBC><pAliqAplic>5.00</pAliqAplic><vISSQN>{iss}</vISSQN>
      <vLiq>{valor}</vLiq></valores>
    <DPS xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00">
      <infDPS Id="DPS{chave_dps}">
        <tpAmb>2</tpAmb>
        <dhEmi>2026-10-{dia}T10:14:30-03:00</dhEmi>
        <serie>1</serie>
        <nDPS>{numero}</nDPS>
        <dCompet>2026-10-{dia}</dCompet>
        <prest><CNPJ>{prest_doc}</CNPJ><xNome>{prest_nome}</xNome></prest>
        <toma><CNPJ>{toma_doc}</CNPJ><xNome>{toma_nome}</xNome></toma>
        {interm}
        <serv><cServ><cTribNac>010101</cTribNac>
          <xDescServ>SERVICO SINTETICO DE DEMONSTRACAO</xDescServ></cServ></serv>
        <valores>
          <vServPrest><vServ>{valor}</vServ></vServPrest>
          <trib><tribMun><tribISSQN>1</tribISSQN><tpRetISSQN>1</tpRetISSQN>
            <pAliq>5.00</pAliq><vISSQN>{iss}</vISSQN></tribMun></trib>
        </valores>
      </infDPS>
    </DPS>
  </infNFSe>
</NFSe>
"""


def _chave(numero: int, cnpj: str) -> str:
    """Chave sintética de 50 dígitos (sem DV: a NFS-e Nacional só exige 50 dígitos)."""
    return "3550308" + "2" + "1" + cnpj + f"{numero:013d}" + "2610" + f"{numero:09d}" + "0"


def _xml(
    numero: int,
    prest: tuple[str, str],
    toma: tuple[str, str],
    valor: str,
    *,
    interm: tuple[str, str] | None = None,
    dia: str = "15",
) -> tuple[str, bytes]:
    chave = _chave(numero, prest[1])
    iss = f"{Decimal(valor) * Decimal('0.05'):.2f}"
    bloco = f"<interm><CNPJ>{interm[1]}</CNPJ><xNome>{interm[0]}</xNome></interm>" if interm else ""
    xml = _MODELO_NFSE.format(
        chave=chave,
        chave_dps=f"DPS{numero:040d}",
        numero=numero,
        dia=dia,
        prest_doc=prest[1],
        prest_nome=prest[0],
        toma_doc=toma[1],
        toma_nome=toma[0],
        valor=valor,
        iss=iss,
        interm=bloco,
    )
    return chave, xml.encode("utf-8")


def _evento_cancelamento(chave: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<evento xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00">'
        f"<infEvento><chNFSe>{chave}</chNFSe><tpAmb>2</tpAmb>"
        "<dhEvento>2026-10-20T09:00:00-03:00</dhEvento><nPedRegEvento>1</nPedRegEvento>"
        "<xMotivo>Cancelamento sintetico de demonstracao</xMotivo></infEvento></evento>"
    ).encode()


def montar_itens_demo() -> list[ItemADN]:
    """2 emitidas, 2 tomadas, 1 com o CNPJ como intermediário, 1 cancelada, 1 DPS (ignorada)."""
    itens: list[ItemADN] = []
    nsu = 0

    def _add(chave: str, xml: bytes, tipo: str = "NFSE", tipo_evento: str | None = None) -> None:
        nonlocal nsu
        nsu += 1
        itens.append(
            ItemADN(
                nsu=nsu,
                chave_acesso=chave,
                tipo_documento=tipo,
                tipo_evento=tipo_evento,
                arquivo_xml=compactar_xml_para_doc_zip_b64(xml),
                data_hora_geracao=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
            )
        )

    c, x = _xml(101, _ALVO, _GAMA, "1500.00")
    _add(c, x)
    c, x = _xml(102, _ALVO, _DELTA, "2300.50", dia="16")
    _add(c, x)
    c, x = _xml(201, _GAMA, _ALVO, "2000.00", dia="17")
    _add(c, x)
    c, x = _xml(202, _DELTA, _ALVO, "800.00", dia="18")
    _add(c, x)
    c, x = _xml(301, _GAMA, _DELTA, "450.00", interm=_ALVO, dia="19")
    _add(c, x)
    c_cancelada, x = _xml(103, _ALVO, _GAMA, "999.00", dia="20")
    _add(c_cancelada, x)
    _add(c_cancelada, _evento_cancelamento(c_cancelada), "EVENTO", "CANCELAMENTO")
    _add("", b"<dps/>", "DPS")
    return itens


def criar_consultor_demo(
    ambiente: Literal[1, 2] = 2,
) -> Callable[[int], RespostaADN]:
    """Consultor sem rede: uma página com os itens sintéticos e depois `404` (fim da fila)."""
    itens = montar_itens_demo()

    def _consultar(nsu: int) -> RespostaADN:
        pendentes = [i for i in itens if (i.nsu or 0) > nsu]
        if not pendentes:
            return RespostaADN(
                http_status=404,
                tipo_consulta="CnpjBase",
                status_processamento="NENHUM_DOCUMENTO_LOCALIZADO",
            )
        return RespostaADN(
            http_status=200,
            tipo_consulta="CnpjBase",
            status_processamento="DOCUMENTOS_LOCALIZADOS",
            lote=pendentes,
            tipo_ambiente="PRODUCAO" if ambiente == 1 else "HOMOLOGACAO",
        )

    return _consultar
