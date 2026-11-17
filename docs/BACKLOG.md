# Backlog do Projeto: `nfe-local`

Este backlog segue a regra de **uma tarefa por sessão de execução no VS Code**, com **TDD (teste antes do código)**, validação com `ruff` e `pytest`, e **um commit pequeno por tarefa**.

---

## Fase 1 — MVP (100% Local, Determinístico e Sem Banco de Dados)

- [x] **1. Estrutura do repositório, `pyproject.toml`, `ruff`, `pytest` e CI no GitHub Actions**
  - Criar a árvore de pacotes (`parsers/`, `models/`, `validators/`, `reports/`, `sefaz/`, `api/`, `web/`, `tests/fixtures/`, `docs/`).
  - Configurar `pyproject.toml` (Python >= 3.12, `ruff`, `pytest`, script CLI `nfe-local`), `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `.env.example` e `LICENSE` (MIT).
  - Criar workflow `.github/workflows/ci.yml` rodando `ruff check`, `ruff format --check` e `pytest`.
  - Criar um teste inicial de fumaça (`tests/test_smoke.py`) garantindo que o ambiente e o pacote importam perfeitamente.

- [x] **2. Fixtures: XMLs fictícios de NF-e (55), NFC-e (65) e NFS-e Nacional (válidos e com defeitos propositais)**
  - Criar gerador determinístico e arquivos XML em `tests/fixtures/` 100% fictícios (zero dados reais):
    - `nfe_55_valida.xml` (com protocolo `nfeProc`, múltiplos itens, ICMS, IPI, PIS, COFINS, frete, desconto e duplicatas).
    - `nfe_55_sem_protocolo.xml` (apenas `<NFe>` sem `<protNFe>`).
    - `nfe_55_cnpj_alfanumerico.xml` (CNPJ e chave de acesso com letras segundo a NT 2014.002 v1.40, com DV válido).
    - `nfe_55_homologacao.xml` (`tpAmb=2`).
    - `nfe_55_soma_errada.xml` (soma dos `vProd` dos itens divergente do `ICMSTot/vProd` e `vNF`).
    - `nfce_65_valida.xml` (modelo 65, consumidor final, pagamento em PIX/Cartão em `<pag>/<detPag>`).
    - `nfse_nacional_valida.xml` (Padrão Nacional `sped.fazenda.gov.br/nfse`, com `infNFSe`, `infDPS`, `ISSQN`, retenções federais e `IBSCBS`).
    - `nfe_resumo.xml` (`<resNFe>`) e `nfe_evento_cancelamento.xml` (`<procEventoNFe>`).
    - `xml_malformado.xml` e `xml_xxe_attack.xml` (tentativa de leitura de entidade externa `<!ENTITY xxe SYSTEM "file:///...">`).
  - Testes garantindo que todas as fixtures existem e que nenhuma contém CNPJ/chave real.

- [x] **3. Modelos Pydantic v2 e normalização segura de valores (`models/`)**
  - Implementar `models/normalizadores.py`: conversão segura para `Decimal`, parsing de datas ISO (`dhEmi`, `dEmi`, `dCompet`), limpeza de CNPJ/CPF e Chave de Acesso preservando caracteres alfanuméricos `[0-9A-Z]`.
  - Implementar `models/fiscal.py`: `DocumentoFiscal`, `Parte`, `Item`, `ImpostoItem`, `ImpostoTotal`, `Totais`, `Pagamento`, `AlertaValidacao` e `ResultadoAnalise`.
  - Testes unitários de serialização, validação de tipos e normalização de valores.

- [x] **4. Parser seguro (`lxml` anti-XXE) de NF-e (55) e NFC-e (65), com testes (`parsers/nfe.py`)**
  - Implementar `parsers/xml_seguro.py` com `lxml.etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False, load_dtd=False)` e helpers agnósticos de namespace.
  - Implementar `parsers/nfe.py` suportando `nfeProc` (com protocolo), `NFe` (sem protocolo), `resNFe` (resumo) e `procEventoNFe`/`evento`, extraindo itens, impostos (`ICMS`, `IPI`, `PIS`, `COFINS`), totais, duplicatas e pagamentos (`detPag`).
  - Proteger contra `<infNFe>` aninhado dentro de CT-e (exigir filho `<ide>`).
  - Testes cobrindo namespaces variados, notas com/sem protocolo, NFC-e, bloqueio de ataque XXE e XML malformado.

- [x] **5. Motor de Validadores, um teste por regra (`validators/`)**
  - Regra 1: Chave de acesso com 44 caracteres (numéricos ou alfanuméricos) e dígito verificador Módulo 11 válido (`ord(c) - 48`).
  - Regra 2: CPF (11 dígitos) e CNPJ (14 caracteres `[0-9A-Z]` conforme NT 2014.002 v1.40) válidos para emitente e destinatário.
  - Regra 3: Batimento financeiro ($\sum \text{itens} == \text{vProd}$ e consistência da equação do `vNF`).
  - Regra 4: Chave de acesso duplicada dentro do mesmo lote.
  - Regra 5: Alerta explícito para nota emitida em ambiente de homologação (`ambiente == 2`) e nota sem protocolo de autorização.
  - Regra 6: XML malformado ou layout desconhecido reportado como `erro_fatal` claro em `ResultadoAnalise`, sem derrubar o processamento do lote.

- [x] **6. Parser de NFS-e do Padrão Nacional e Detector Automático de Documento (`parsers/nfse_nacional.py` e `parsers/detector.py`)**
  - Implementar `parsers/nfse_nacional.py` lendo `infNFSe` e `infDPS` (chave de 50 dígitos, prestador, tomador, serviço `cTribNac`, `vServ`, `vLiq`, `vISSQN`, retenções federais `IRRF`/`CSLL`/`CP`/`PIS`/`COFINS` e bloco `IBSCBS`), convertendo para `DocumentoFiscal`.
  - Implementar `parsers/detector.py`: recebe `bytes` (ou caminho/stream de XML ou `.zip`), detecta automaticamente NF-e (55), NFC-e (65), NFS-e Nacional, Resumo ou Evento, faz o parse e passa pelos validadores.
  - Rejeitar com mensagem clara layouts municipais legados (ex.: ABRASF) informando que apenas o Padrão Nacional da NFS-e é suportado.

- [x] **7. Resumo de lote e processamento eficiente em memória (`reports/resumo.py`)**
  - Implementar agregador de lote capaz de processar iteradores/streams de notas (meta: 1.000+ XMLs sem carregar todas as árvores DOM na memória simultaneamente).
  - Gerar totais consolidados:
    - Por emitente (CNPJ, razão social, quantidade de notas, valor total, impostos).
    - Por imposto (`ICMS`, `ICMS-ST`, `IPI`, `PIS`, `COFINS`, `ISS`, `Retenções`, `IBS/CBS`).
    - Por período (agrupamento diário e mensal `YYYY-MM`).
    - Contagem de notas válidas, com avisos, com erros e rejeitadas/ilegíveis.

- [x] **8. Exportação em CSV e XLSX (`reports/exportacao.py`)**
  - Exportar o lote processado em CSV (UTF-8 com BOM para abrir direto no Excel em pt-BR) e em XLSX (`openpyxl`) com abas organizadas:
    - Aba 1: `Notas` (cabeçalho, emitente, destinatário, totais, status de validação).
    - Aba 2: `Itens` (chave da nota, número do item, código, descrição, NCM, CFOP, qtd, valores e impostos do item).
    - Aba 3: `Resumo` (totais por emitente, por imposto e por período).
    - Aba 4: `Alertas` (todos os erros e avisos apontados pelos validadores).

- [x] **9. API REST FastAPI e CLI (`api/` e entrypoint CLI)**
  - Criar endpoints FastAPI:
    - `POST /api/lote`: upload de 1 ou vários arquivos `.xml` e/ou `.zip` (com proteção contra zip bomb e extensões inválidas), retornando o `ResumoLote` e a lista de `ResultadoAnalise`.
    - `GET /api/lote/{lote_id}` e `GET /api/lote/{lote_id}/nota/{chave}`: detalhe completo da nota.
    - `GET /api/lote/{lote_id}/exportar?formato=csv|xlsx`: download da planilha consolidada.
  - Adicionar subcomandos na CLI (`nfe-local analisar <arquivos_ou_pasta> --json --exportar-xlsx <saida.xlsx>`) para uso direto no terminal ou por agentes de IA.
  - Testes de integração com `TestClient` do FastAPI e da CLI.

- [x] **10. Interface Web: Upload, Lista, Resumo, DANFE e DANFSe (`web/` e `reports/danfe_svg.py`)**
  - Criar interface web limpa e responsiva (HTML/CSS/JS puros servidos pelo FastAPI, com suporte a Dark Mode):
    - Área de drag-and-drop para múltiplos XMLs e arquivos `.zip`.
    - Cards de resumo do lote (totais por emitente, imposto e período) e botões de exportação CSV/XLSX.
    - Tabela filtrável de notas com badges claros para erros/avisos de validação (ex.: `HOMOLOGAÇÃO`, `SOMA DIVERGENTE`, `CHAVE DUPLICADA`).
  - Implementar visualizador imprimível A4 (fiel ao papel):
    - **DANFE** (NF-e/NFC-e) com código de barras **Code 128** em SVG puro (`reports/danfe_svg.py`).
    - **DANFSe** (NFS-e Padrão Nacional conforme NT 008/2026).

- [x] **11. `Dockerfile`, scripts Windows (`instalar.bat`/`iniciar.bat`), Benchmark de 1.000 XMLs e `README.md` / `README.en.md`**
  - Criar `Dockerfile` enxuto e seguro (usuário não-root) e `.dockerignore`.
  - Criar `instalar.bat` e `iniciar.bat` seguindo o padrão amigável do `transcritor-local`.
  - Criar script de benchmark (`scripts/benchmark_lote.py`) que gera 1.000 XMLs sintéticos em memória/temp, mede tempo total, throughput (notas/s) e pico de memória (`tracemalloc`), e documentar em `docs/benchmark.md`.
  - Escrever `README.md` (Português) e `README.en.md` (Inglês) com prints/GIF simulados, instruções de uso, tabela de estado do projeto, limitações conhecidas e seção **"Como usei IA"**.

- [x] **12. (Opcional) Perguntas sobre o lote com LLM, com cache e limite de uso**
  - Módulo opcional ativado apenas se variável de ambiente do provedor LLM estiver configurada.
  - Recebe uma pergunta sobre o resumo/itens do lote (enviando apenas dados agregados/estruturados, com cache em disco por hash do lote+pergunta e rate-limit configurável).

---

## Fase 2 — Notas Emitidas Contra o CNPJ da Empresa (`NFeDistribuicaoDFe`)

- [x] **13. Leitor de Certificado A1 e Cliente SOAP do `NFeDistribuicaoDFe` em homologação, escrito do zero (`sefaz/cert_a1.py` e `sefaz/dist_dfe.py`)**
  - Implementar `sefaz/cert_a1.py`: leitura do `.pfx` (PKCS#12) via `cryptography` a partir de variáveis de ambiente (`NFE_CERT_PATH` e `NFE_CERT_SENHA`), extraindo CNPJ (OID ICP-Brasil `2.16.76.1.3.3` ou CN), titular, emissor e validade UTC sem gravar chave privada desencriptada em disco.
  - Implementar `sefaz/dist_dfe.py`: montagem do envelope SOAP 1.2 (`nfeDistDFeInteresse` / `distDFeInt_v1.01.xsd`) e chamada HTTPS com mTLS (`requests-pkcs12` ou `ssl.SSLContext`) apontando para o Ambiente Nacional da NF-e (homologação por padrão, produção configurável).

- [x] **14. Controle de NSU e de espera: guardar `ultNSU` e respeitar os retornos `137`, `138` e `656`**
  - Parsear o retorno SOAP `<retDistDFeInt>` extraindo `cStat`, `xMotivo`, `ultNSU` e `maxNSU`.
  - Implementar máquina de estados de sincronização:
    - `cStat == 138`: documentos localizados; avançar `ultNSU` e continuar enquanto `ultNSU < maxNSU`.
    - `cStat == 137`: nenhum documento localizado; registrar timestamp e encerrar rodada.
    - `cStat == 656`: consumo indevido; gravar trava obrigatória (`bloqueado_ate = agora + 1 hora`) para impedir qualquer nova requisição antes de 60 minutos (evitando reset do bloqueio na SEFAZ).

- [x] **15. Descompactar as respostas (`docZip`) e passar as notas pelo parser existente (`sefaz/descompactador.py`)**
  - Decodificar cada `<docZip NSU="..." schema="...">` (`base64` -> `gzip.decompress`).
  - Encaminhar os bytes XML descompactados para `parsers/detector.py`, identificando resumos (`resNFe`), notas completas (`procNFe`) e eventos (`procEventoNFe`).
  - Sinalizar claramente quando uma nota veio apenas como `resNFe` (exigindo Manifestação do Destinatário para liberar o XML completo).

- [ ] **16. Persistência mínima em SQLite para NSU e notas recebidas (`sefaz/storage.py`)**
  - Criar banco SQLite local (fora do Git, em `data/nfe_recebidas.db`) com tabelas:
    - `dfe_cursor` (`ambiente`, `ultimo_nsu`, `max_nsu`, `ultima_busca`, `bloqueado_ate`).
    - `notas_recebidas` (`chave`, `nsu`, `schema`, `tipo`, `emitente_cnpj`, `emitente_nome`, `data_emissao`, `valor_total`, `situacao`, `manifestacao_necessaria`, `xml_gzip`, `atualizado_em`).
    - `sync_log` (`id`, `ts`, `ambiente`, `cstat`, `motivo`, `novos_docs`).

- [ ] **17. Tela "Recebidas": lista filtrável por emitente, período e valor**
  - Adicionar aba/tela **"Recebidas (SEFAZ)"** na interface web e rotas na API:
    - Card de status do Certificado Digital A1 (titular, CNPJ, dias para vencer) e status do cursor NSU / bloqueio `656`.
    - Botão "Sincronizar agora" (respeitando o lock e o backoff de 1h).
    - Lista de notas recebidas filtrável por emitente, período, faixa de valor e tipo (`Completa` vs `Resumo`), abrindo o DANFE para as notas completas.

- [ ] **18. Testes com respostas simuladas (`138`, `137` e `656`), sem tocar na SEFAZ**
  - Criar fixtures de envelopes SOAP reais da estrutura `<retDistDFeInt>` (com dados fictícios compactados em `gzip+base64`) para `cStat 138` (múltiplas páginas até `ultNSU == maxNSU`), `cStat 137` e `cStat 656`.
  - Testar toda a cadeia (SOAP client mockado -> descompactador -> parser -> SQLite -> trava de 1h do `656` -> tela Recebidas) 100% offline.

- [ ] **19. Documentar o manuseio seguro do certificado no README (PT e EN)**
  - Documentar no `README.md` e `README.en.md` como configurar o certificado e-CNPJ A1 via variáveis de ambiente, garantias de que o `.pfx` nunca entra no Git nem na imagem Docker, funcionamento do `NFeDistribuicaoDFe` (NSU, janela de 90 dias da SEFAZ, manifestação do destinatário e trava do erro `656`).
