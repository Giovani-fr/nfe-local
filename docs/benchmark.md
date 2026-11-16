# Benchmark de Processamento em Lote (1.000 XMLs)

Este documento registra a metodologia e os resultados reais de desempenho e uso de memória do `nfe-local` ao processar um lote de **1.000 documentos fiscais eletrônicos sintéticos** (misturando NF-e modelo 55, NFC-e modelo 65, NF-e com CNPJ Alfanumérico da NT 2014.002 v1.40 e NFS-e Padrão Nacional de 50 dígitos).

---

## 1. Ambiente de Medição

- **Script reproduzível:** [`scripts/benchmark_lote.py`](../scripts/benchmark_lote.py)
- **Sistema Operacional:** `Windows-11-10.0.26200-SP0` (64-bit)
- **Processador:** `Intel64 Family 6 Model 186 Stepping 2, GenuineIntel`
- **Python:** `3.12.10` (`lxml`, `pydantic` v2, `openpyxl`)
- **Instrumentação:** `time.perf_counter()` para tempo de parede e `tracemalloc` para pico de memória alocada no heap do Python.

---

## 2. Composição da Carga (1.000 XMLs Sintéticos)

O gerador determinístico cria 1.000 documentos únicos (zero chaves duplicadas, dígitos verificadores calculados matematicamente e somas financeiras consistentes):

| Tipo de Documento | Quantidade | Características Testadas |
| :--- | :---: | :--- |
| **NF-e (Modelo 55 — Numérico)** | 250 | Chave de 44 dígitos, ICMS, IPI, PIS, COFINS e protocolo `nfeProc`. |
| **NFC-e (Modelo 65 — Varejo)** | 250 | Consumidor final CPF, ICMS, PIS e COFINS. |
| **NF-e (Modelo 55 — CNPJ Alfanumérico)** | 250 | CNPJ `12.ABC.345/01DE-35` na chave de acesso e DV Módulo 11 (`ord(c) - 48`). |
| **NFS-e (Padrão Nacional)** | 250 | Chave de 50 dígitos, `infNFSe` + `infDPS`, `ISSQN` e bloco `IBSCBS` (Reforma Tributária). |
| **Total** | **1.000** | **1.000 documentos válidos analisados** |

---

## 3. Resultados Medidos

| Cenário Medido | Tempo Total (s) | Throughput (XMLs/s) | Pico de Memória (`tracemalloc`) | Artefato Gerado |
| :--- | :---: | :---: | :---: | :---: |
| **1. Modo Streaming (`iterar_lote` + `gerar_resumo_lote`)** | **5,92 s** | **169,1 XMLs/s** | **0,63 MB** | Objeto `ResumoLote` consolidado |
| **2. Lote Completo + Exportação XLSX (4 Abas `openpyxl`)** | **14,56 s** | **68,7 XMLs/s** | **32,44 MB** | Planilha `.xlsx` de **275,6 KB** |

---

## 4. Análise Arquitetural

1. **Memória $O(1)$ em árvores DOM no modo Streaming (`0,63 MB`):**
   - Como `iterar_lote` e `AcumuladorResumoLote` consomem geradores Python (`Iterator`), cada árvore XML do `lxml` é descartada assim que o modelo Pydantic `ResultadoAnalise` é consolidado nos somatórios de emitente, imposto e período. Isso permite analisar lotes de milhares de XMLs em máquinas modestas sem estourar a RAM.
2. **Custo de geração do XLSX (`openpyxl`):**
   - No cenário 2, o tempo adicional (~8,6 s) e o pico de memória (`32,44 MB`) concentram-se na construção em memória do `Workbook` do `openpyxl` com 4 abas estilizadas (`Notas`, `Itens`, `Resumo` e `Alertas`), formatação numérica e blindagem de células de texto (`@`) para chaves de 44/50 posições.

---

## 5. Como Reproduzir na Sua Máquina

```powershell
python scripts/benchmark_lote.py
```
