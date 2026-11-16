"""Benchmark determinístico de 1.000 XMLs sintéticos (`scripts/benchmark_lote.py`)."""

from __future__ import annotations

import json
import platform
import sys
import time
import tracemalloc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROJETO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROJETO))

from parsers.detector import iterar_lote, processar_lote  # noqa: E402
from reports.exportacao import exportar_lote_xlsx  # noqa: E402
from reports.resumo import gerar_resumo_lote  # noqa: E402
from validators.chave_acesso import calcular_dv_chave_44  # noqa: E402


def _gerar_chave_44(cuf: str, aamm: str, cnpj: str, mod: str, serie: int, nnf: int) -> str:
    cnf = f"{10000000 + (nnf % 89999999):08d}"
    base_43 = f"{cuf}{aamm}{cnpj}{mod}{serie:03d}{nnf:09d}1{cnf}"
    dv = calcular_dv_chave_44(base_43)
    return f"{base_43}{dv}"


def gerar_xml_sintetico(indice: int) -> tuple[str, bytes]:
    """Gera um XML sintético válido (NF-e 55, NFC-e 65, NF-e Alfanumérica ou NFS-e Nacional)."""
    nnf = 10000 + indice
    tipo_mod = indice % 4

    if tipo_mod == 0:
        cnpj = "11222333000181"
        chave = _gerar_chave_44("35", "2603", cnpj, "55", 1, nnf)
        cnf = chave[35:43]
        cdv = chave[43]
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe{chave}" versao="4.00">
      <ide>
        <cUF>35</cUF><cNF>{cnf}</cNF><natOp>VENDA DE PRODUCAO</natOp>
        <mod>55</mod><serie>1</serie><nNF>{nnf}</nNF>
        <dhEmi>2026-03-10T14:30:00-03:00</dhEmi><tpNF>1</tpNF>
        <cDV>{cdv}</cDV><tpAmb>1</tpAmb>
      </ide>
      <emit>
        <CNPJ>{cnpj}</CNPJ><xNome>INDUSTRIA ALFA METALURGICA LTDA</xNome>
        <enderEmit><UF>SP</UF></enderEmit>
      </emit>
      <dest>
        <CNPJ>22333444000181</CNPJ><xNome>COMERCIO BETA LTDA</xNome>
        <enderDest><UF>SP</UF></enderDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>P{indice:04d}</cProd><xProd>ITEM SINTETICO {indice}</xProd>
          <NCM>73269090</NCM><CFOP>5101</CFOP><uCom>UN</uCom>
          <qCom>2.0000</qCom><vUnCom>150.0000</vUnCom><vProd>300.00</vProd>
        </prod>
        <imposto>
          <ICMS>
            <ICMS00><CST>00</CST><vBC>300.00</vBC><pICMS>18.00</pICMS><vICMS>54.00</vICMS></ICMS00>
          </ICMS>
          <IPI><IPITrib><CST>50</CST><pIPI>5.00</pIPI><vIPI>15.00</vIPI></IPITrib></IPI>
          <PIS><PISAliq><CST>01</CST><vPIS>4.95</vPIS></PISAliq></PIS>
          <COFINS><COFINSAliq><CST>01</CST><vCOFINS>22.80</vCOFINS></COFINSAliq></COFINS>
        </imposto>
      </det>
      <total>
        <ICMSTot>
          <vBC>300.00</vBC><vICMS>54.00</vICMS><vBCST>0.00</vBCST><vST>0.00</vST>
          <vProd>300.00</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc>
          <vIPI>15.00</vIPI><vPIS>4.95</vPIS><vCOFINS>22.80</vCOFINS>
          <vOutro>0.00</vOutro><vNF>315.00</vNF>
        </ICMSTot>
      </total>
    </infNFe>
  </NFe>
  <protNFe versao="4.00">
    <infProt>
      <tpAmb>1</tpAmb><chNFe>{chave}</chNFe><nProt>13526{nnf:010d}</nProt>
      <cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo>
    </infProt>
  </protNFe>
</nfeProc>"""
        return f"nfe_55_{indice:04d}.xml", xml.encode("utf-8")

    if tipo_mod == 1:
        cnpj = "22333444000181"
        chave = _gerar_chave_44("35", "2603", cnpj, "65", 1, nnf)
        cnf = chave[35:43]
        cdv = chave[43]
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe{chave}" versao="4.00">
      <ide>
        <cUF>35</cUF><cNF>{cnf}</cNF><natOp>VENDA AO CONSUMIDOR</natOp>
        <mod>65</mod><serie>1</serie><nNF>{nnf}</nNF>
        <dhEmi>2026-03-15T18:45:00-03:00</dhEmi><tpNF>1</tpNF>
        <cDV>{cdv}</cDV><tpAmb>1</tpAmb>
      </ide>
      <emit>
        <CNPJ>{cnpj}</CNPJ><xNome>COMERCIO BETA DISTRIBUIDORA LTDA</xNome>
        <enderEmit><UF>SP</UF></enderEmit>
      </emit>
      <dest><CPF>12345678909</CPF><xNome>CONSUMIDOR FICTICIO</xNome></dest>
      <det nItem="1">
        <prod>
          <cProd>V{indice:04d}</cProd><xProd>PRODUTO VAREJO {indice}</xProd>
          <NCM>82041100</NCM><CFOP>5102</CFOP><uCom>UN</uCom>
          <qCom>1.0000</qCom><vUnCom>120.0000</vUnCom><vProd>120.00</vProd>
        </prod>
        <imposto>
          <ICMS>
            <ICMS00><CST>00</CST><vBC>120.00</vBC><pICMS>18.00</pICMS><vICMS>21.60</vICMS></ICMS00>
          </ICMS>
          <PIS><PISAliq><CST>01</CST><vPIS>1.98</vPIS></PISAliq></PIS>
          <COFINS><COFINSAliq><CST>01</CST><vCOFINS>9.12</vCOFINS></COFINSAliq></COFINS>
        </imposto>
      </det>
      <total>
        <ICMSTot>
          <vBC>120.00</vBC><vICMS>21.60</vICMS><vBCST>0.00</vBCST><vST>0.00</vST>
          <vProd>120.00</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc>
          <vIPI>0.00</vIPI><vPIS>1.98</vPIS><vCOFINS>9.12</vCOFINS>
          <vOutro>0.00</vOutro><vNF>120.00</vNF>
        </ICMSTot>
      </total>
    </infNFe>
  </NFe>
  <protNFe versao="4.00">
    <infProt>
      <tpAmb>1</tpAmb><chNFe>{chave}</chNFe><nProt>13526{nnf:010d}</nProt>
      <cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo>
    </infProt>
  </protNFe>
</nfeProc>"""
        return f"nfce_65_{indice:04d}.xml", xml.encode("utf-8")

    if tipo_mod == 2:
        cnpj = "12ABC34501DE35"
        chave = _gerar_chave_44("35", "2607", cnpj, "55", 1, nnf)
        cnf = chave[35:43]
        cdv = chave[43]
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe{chave}" versao="4.00">
      <ide>
        <cUF>35</cUF><cNF>{cnf}</cNF><natOp>VENDA CNPJ ALFANUMERICO</natOp>
        <mod>55</mod><serie>1</serie><nNF>{nnf}</nNF>
        <dhEmi>2026-07-15T10:00:00-03:00</dhEmi><tpNF>1</tpNF>
        <cDV>{cdv}</cDV><tpAmb>1</tpAmb>
      </ide>
      <emit>
        <CNPJ>{cnpj}</CNPJ><xNome>INOVACAO DELTA COMPONENTES S.A.</xNome>
        <enderEmit><UF>SP</UF></enderEmit>
      </emit>
      <dest>
        <CNPJ>22333444000181</CNPJ><xNome>COMERCIO BETA LTDA</xNome>
        <enderDest><UF>SP</UF></enderDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>ALN{indice:04d}</cProd><xProd>COMPONENTE ALFANUMERICO {indice}</xProd>
          <NCM>90318099</NCM><CFOP>5101</CFOP><uCom>UN</uCom>
          <qCom>4.0000</qCom><vUnCom>200.0000</vUnCom><vProd>800.00</vProd>
        </prod>
        <imposto>
          <ICMS>
            <ICMS00><CST>00</CST><vBC>800.00</vBC><pICMS>18.00</pICMS><vICMS>144.00</vICMS></ICMS00>
          </ICMS>
          <PIS><PISAliq><CST>01</CST><vPIS>13.20</vPIS></PISAliq></PIS>
          <COFINS><COFINSAliq><CST>01</CST><vCOFINS>60.80</vCOFINS></COFINSAliq></COFINS>
        </imposto>
      </det>
      <total>
        <ICMSTot>
          <vBC>800.00</vBC><vICMS>144.00</vICMS><vBCST>0.00</vBCST><vST>0.00</vST>
          <vProd>800.00</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc>
          <vIPI>0.00</vIPI><vPIS>13.20</vPIS><vCOFINS>60.80</vCOFINS>
          <vOutro>0.00</vOutro><vNF>800.00</vNF>
        </ICMSTot>
      </total>
    </infNFe>
  </NFe>
  <protNFe versao="4.00">
    <infProt>
      <tpAmb>1</tpAmb><chNFe>{chave}</chNFe><nProt>13526{nnf:010d}</nProt>
      <cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo>
    </infProt>
  </protNFe>
</nfeProc>"""
        return f"nfe_alnum_{indice:04d}.xml", xml.encode("utf-8")

    chave_nfse = f"355030822334445550001810000000000{nnf:05d}260300000010"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00">
  <infNFSe Id="NFS{chave_nfse}" versao="1.00">
    <xLocEmi>SAO PAULO</xLocEmi>
    <nNFSe>{nnf}</nNFSe>
    <cStat>100</cStat>
    <dhProc>2026-03-16T10:15:00-03:00</dhProc>
    <emit>
      <CNPJ>33444555000181</CNPJ><xNome>SERVICOS TECNOLOGIA GAMA SA</xNome>
      <enderNac><UF>SP</UF></enderNac>
    </emit>
    <valores>
      <vBC>1000.00</vBC><pAliqAplic>5.00</pAliqAplic><vISSQN>50.00</vISSQN>
      <vTotalRet>0.00</vTotalRet><vLiq>1000.00</vLiq>
    </valores>
    <IBSCBS>
      <totCIBS>
        <gIBS><vIBSTot>10.00</vIBSTot></gIBS><gCBS><vCBS>9.00</vCBS></gCBS>
        <vTotNF>1000.00</vTotNF>
      </totCIBS>
    </IBSCBS>
    <DPS xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00">
      <infDPS Id="DPS3550308233444555000181000010000000000{nnf:05d}">
        <tpAmb>1</tpAmb><dhEmi>2026-03-16T10:14:30-03:00</dhEmi>
        <serie>1</serie><nDPS>{nnf}</nDPS><dCompet>2026-03-16</dCompet>
        <prest><CNPJ>33444555000181</CNPJ><xNome>SERVICOS TECNOLOGIA GAMA SA</xNome></prest>
        <toma><CNPJ>11222333000181</CNPJ><xNome>INDUSTRIA ALFA METALURGICA LTDA</xNome></toma>
        <serv>
          <cServ>
            <cTribNac>010101</cTribNac>
            <xDescServ>SERVICO TECNICO SINTETICO {indice}</xDescServ>
          </cServ>
        </serv>
        <valores>
          <vServPrest><vServ>1000.00</vServ></vServPrest>
          <trib>
            <tribMun><tribISSQN>1</tribISSQN><pAliq>5.00</pAliq><vISSQN>50.00</vISSQN></tribMun>
          </trib>
        </valores>
      </infDPS>
    </DPS>
  </infNFSe>
</NFSe>"""
    return f"nfse_nac_{indice:04d}.xml", xml.encode("utf-8")


def iterar_xmls_sinteticos(quantidade: int) -> Iterator[tuple[str, bytes]]:
    """Gera `quantidade` pares `(nome_arquivo, xml_bytes)` sob demanda (streaming)."""
    for idx in range(quantidade):
        yield gerar_xml_sintetico(idx)


def executar_benchmark(quantidade: int = 1000) -> dict[str, Any]:
    """Executa o benchmark nos modos Streaming e Lote Completo + XLSX, medindo tempo e memória."""
    # 1. Modo Streaming (O(1) em árvores DOM)
    tracemalloc.start()
    t0 = time.perf_counter()
    resumo_stream = gerar_resumo_lote(iterar_lote(iterar_xmls_sinteticos(quantidade)))
    tempo_streaming_s = time.perf_counter() - t0
    _, pico_streaming_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Pré-carrega entradas para medir processamento de lote completo + XLSX
    entradas_memoria = list(iterar_xmls_sinteticos(quantidade))

    # 2. Modo Lote Completo + Exportação XLSX Multi-abas
    tracemalloc.start()
    t1 = time.perf_counter()
    resultados_lote = processar_lote(entradas_memoria)
    resumo_lote = gerar_resumo_lote(resultados_lote)
    xlsx_bytes = exportar_lote_xlsx(resultados_lote, resumo=resumo_lote)
    tempo_lote_xlsx_s = time.perf_counter() - t1
    _, pico_lote_xlsx_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "quantidade_xmls": quantidade,
        "validos": resumo_stream.contagem.validos,
        "python_version": platform.python_version(),
        "plataforma": platform.platform(),
        "processador": platform.processor(),
        "streaming": {
            "tempo_s": round(tempo_streaming_s, 4),
            "xmls_por_segundo": round(quantidade / max(tempo_streaming_s, 1e-6), 1),
            "pico_memoria_mb": round(pico_streaming_bytes / (1024 * 1024), 2),
        },
        "lote_completo_xlsx": {
            "tempo_s": round(tempo_lote_xlsx_s, 4),
            "xmls_por_segundo": round(quantidade / max(tempo_lote_xlsx_s, 1e-6), 1),
            "pico_memoria_mb": round(pico_lote_xlsx_bytes / (1024 * 1024), 2),
            "tamanho_xlsx_kb": round(len(xlsx_bytes) / 1024, 1),
        },
    }


if __name__ == "__main__":
    metricas = executar_benchmark(1000)
    print(json.dumps(metricas, indent=2, ensure_ascii=False))
