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

- [x] **16. Persistência mínima em SQLite para NSU e notas recebidas (`sefaz/storage.py`)**
  - Criar banco SQLite local (fora do Git, em `data/nfe_recebidas.db`) com tabelas:
    - `dfe_cursor` (`ambiente`, `ultimo_nsu`, `max_nsu`, `ultima_busca`, `bloqueado_ate`).
    - `notas_recebidas` (`chave`, `nsu`, `schema`, `tipo`, `emitente_cnpj`, `emitente_nome`, `data_emissao`, `valor_total`, `situacao`, `manifestacao_necessaria`, `xml_gzip`, `atualizado_em`).
    - `sync_log` (`id`, `ts`, `ambiente`, `cstat`, `motivo`, `novos_docs`).

- [x] **17. Tela "Recebidas": lista filtrável por emitente, período e valor**
  - Adicionar aba/tela **"Recebidas (SEFAZ)"** na interface web e rotas na API:
    - Card de status do Certificado Digital A1 (titular, CNPJ, dias para vencer) e status do cursor NSU / bloqueio `656`.
    - Botão "Sincronizar agora" (respeitando o lock e o backoff de 1h).
    - Lista de notas recebidas filtrável por emitente, período, faixa de valor e tipo (`Completa` vs `Resumo`), abrindo o DANFE para as notas completas.

- [x] **18. Testes com respostas simuladas (`138`, `137` e `656`), sem tocar na SEFAZ**
  - Criar fixtures de envelopes SOAP reais da estrutura `<retDistDFeInt>` (com dados fictícios compactados em `gzip+base64`) para `cStat 138` (múltiplas páginas até `ultNSU == maxNSU`), `cStat 137` e `cStat 656`.
  - Testar toda a cadeia (SOAP client mockado -> descompactador -> parser -> SQLite -> trava de 1h do `656` -> tela Recebidas) 100% offline.

- [x] **19. Documentar o manuseio seguro do certificado no README (PT e EN)**
  - Documentar no `README.md` e `README.en.md` como configurar o certificado e-CNPJ A1 via variáveis de ambiente, garantias de que o `.pfx` nunca entra no Git nem na imagem Docker, funcionamento do `NFeDistribuicaoDFe` (NSU, janela de 90 dias da SEFAZ, manifestação do destinatário e trava do erro `656`).

---

## Fase 3 — Evolução de Produto: Histórico Persistente, Gestão Visual de Certificado A1 e Nova UX

- [x] **20. Histórico Persistente de Lotes e Acervo Unificado de Notas no SQLite (`sefaz/storage.py` e `api/app.py`)**
  - Persistir no SQLite local (`data/nfe_recebidas.db`) todos os lotes e documentos importados via upload (`lotes_importados` e `itens_lote_importado`), mantendo compatibilidade total com os endpoints existentes (`/api/lote/*`).
  - Ao reiniciar o servidor ou recarregar a página, manter todo o histórico de lotes e notas acessível no disco.
  - Criar endpoints para:
    - Listar o histórico de lotes importados (`GET /api/lotes`), com opção de excluir um lote específico (`DELETE /api/lote/{lote_id}`) ou limpar o histórico (`DELETE /api/lotes`).
    - Consultar o **"Acervo Unificado"** (`GET /api/acervo`): consolida todas as notas de todos os lotes já carregados (deduplicando chaves idênticas nos totalizadores financeiros via `gerar_resumo_lote`), permitindo filtrar, exportar CSV/XLSX (`GET /api/acervo/exportar`), perguntar ao assistente IA (`POST /api/acervo/perguntar`) e abrir o DANFE/DANFSe ou baixar o XML de qualquer nota já carregada em qualquer lote.

- [x] **21. Cadastro e Gerenciamento Visual do Certificado Digital A1 na Interface (`sefaz/cert_a1.py` e `api/app.py`)**
  - Permitir que o usuário cadastre o certificado digital `.pfx` / `.p12`, senha, UF (`cUFAutor`) e ambiente (`Homologação` ou `Produção`) diretamente pela interface web, sem precisar editar arquivo `.env` manualmente.
  - Salvar o certificado de forma segura na pasta local ignorada pelo Git (`data/certificado/certificado.pfx` e metadados/configuração local em `data/certificado/config.json`), com nome fixo (proteção contra *path traversal* no nome do arquivo enviado).
  - Validar o par `.pfx + senha` em memória com `cryptography` antes de salvar, retornando imediatamente Titular, CNPJ, Emissor e Validade (dias restantes).
  - Criar endpoints `POST /api/recebidas/certificado` (upload `.pfx` + senha + UF + ambiente), `PATCH /api/recebidas/config` (alternar ambiente Homologação/Produção ou UF) e `DELETE /api/recebidas/certificado` (remover certificado do computador).

- [x] **22. Redesign Completo da Interface do Usuário (UX de Produto Real em `web/`)**
  - Reestruturar a interface web para navegação fluida focada no usuário final:
    - **Seletor de Escopo (Histórico Lateral / Topo):** alternar com 1 clique entre **"📚 Todo o Acervo (Todas as Notas)"** e qualquer lote individual do **"📦 Histórico de Lotes Importados"** (com botão de excluir lote ou limpar demonstração), carregando automaticamente ao abrir a página (`F5` não perde nada).
    - **Importação Inteligente e Compacta:** área de upload que não ocupa metade da tela quando já há notas carregadas (botão/barra compacta `"➕ Adicionar XMLs / ZIP"` com suporte a drag-and-drop global).
    - **Organização Visual em Sub-visões Limpas:** separar claramente a **`📋 Tabela de Notas e Filtros`** (foco operacional imediato com busca, filtro por tipo, status, emitente e período) do **`📊 Painel Gerencial e Impostos`** (KPIs, tributos consolidados, tabelas por emitente e por competência) e do **`🤖 Assistente Analítico`**.
    - **Modal/Painel Visual de Certificado A1 na Aba SEFAZ:** formulário amigável para selecionar o `.pfx`, digitar a senha, escolher UF e Ambiente (Homologação/Produção), ver status de validade e remover o certificado quando desejar.

- [x] **23. Polimento para Uso Real: Tabela sem Rolagem Horizontal, Reset Completo do SQLite e Blindagem Extra no `.gitignore`**
  - **Blindagem anti-vazamento no `.gitignore`:** bloquear qualquer arquivo `*.xml`, `*.zip`, `*.csv` e `*.xlsx` fora de `tests/fixtures/` (`!tests/fixtures/**/*.xml`), garantindo que nenhum arquivo fiscal real ou planilha gerada na pasta do projeto possa ser versionado por acidente.
  - **Tabela de Notas sem barra de rolagem horizontal (`web/static/styles.css` e `web/static/app.js`):** ajustar larguras e quebras das colunas (especialmente a Chave de Acesso de 44/50 dígitos, com opção de copiar chave e quebra limpa) e fixar a visibilidade da coluna **Ações** (`📄 DANFE / DANFSe`, `🔍 Detalhes`, `⬇ XML`) para que fique sempre visível na tela sem precisar rolar para a direita.
  - **Reset Completo do SQLite Local (Aba 1 + Aba 2 + CLI):**
    - Implementar `limpar_notas_recebidas` e `resetar_banco_completo` em `sefaz/storage.py`.
    - Criar endpoints `DELETE /api/recebidas` e `POST /api/sistema/resetar-banco` em `api/app.py` e subcomando CLI `nfe-local resetar-banco`.
    - Adicionar botões na interface web para limpar as notas recebidas da Aba 2 e para **Resetar Todo o Banco Local** (separando testes de dados reais), e deixar o banco local `data/nfe_recebidas.db` zerado ao final da tarefa.

- [x] **24. Ordenação Clicável nas Tabelas (Aba 1 e Aba 2) e Espelhos DANFE / DANFSe no Padrão Oficial**
  - **Ordenação clicável nas tabelas (`web/templates/index.html`, `web/static/styles.css` e `web/static/app.js`):** adicionar coluna `Emissão` na tabela de notas da Aba 1 e cabeçalhos `<th class="sortable" data-sort="...">` em ambas as abas (ordenação inteligente numérica, cronológica e alfabética em memória com indicadores `⇅` / `▲` / `▼`).
  - **DANFE e DANFSe no padrão oficial fiel (`reports/danfe_svg.py`, `api/app.py`, `web/templates/danfe.html`, `web/templates/danfse.html`):** passar `xml_bytes` bruto nas rotas de espelho (`/lote/.../espelho` e `/recebidas/.../espelho`) com fallback para `DocumentoFiscal`, renderizando todos os 9 blocos oficiais do DANFE (canhoto, cabeçalho triplo com `0 - ENTRADA / 1 - SAÍDA`, protocolo, destinatário em 3 linhas, duplicatas/pagamentos, cálculo do imposto em 5+6 caixas, transportador/volumes com Código ANTT, tabela de itens de 14 colunas com `BC ICMS`/`Alíq. ICMS`/`Alíq. IPI` e dados adicionais) e o DANFSe v2.0 (NT 008/2026) completo com domínios oficiais, DPS, Simples Nacional, ISSQN, tributos federais, IBS/CBS e Lei 12.741/2012.

- [x] **25. QR Code Oficial no DANFSe v2.0, Lazy Load de XML no SQLite para Grandes Acervos (15.000+ notas) e Paginação na Tabela**
  - **QR Code SVG puro no DANFSe v2.0 (`reports/danfe_svg.py`, `web/templates/danfse.html`, `pyproject.toml`, `requirements.txt`):** adicionar dependência leve `qrcode>=7.4` (sem Pillow), implementar `qrcode_svg(texto_url)` gerando `<svg shape-rendering="crispEdges">` e exibir o QR Code (`26mm x 26mm`) apontando para o portal nacional (`https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave=...`) no quadro superior direito do DANFSe.
  - **Lazy load no SQLite e escala para 15.000+ XMLs (`parsers/detector.py`, `sefaz/storage.py`, `api/app.py`):** ampliar os limites padrão de extração de ZIP para `50_000` arquivos e `1 GB`; alterar `obter_acervo_unificado(incluir_xmls_brutos=False)` para não ler nem descompactar `xml_gzip` na listagem/resumo; criar índice `idx_lotes_itens_chave` e o método cirúrgico `obter_nota_e_xml_no_escopo(lote_id, identificador)` usado diretamente pelas rotas de detalhe, espelho e download de XML; priorizar `chave_acesso` nos links da tabela Web.
  - **Paginação fluida na tabela de notas (`web/templates/index.html`, `web/static/styles.css`, `web/static/app.js`):** manter filtragem e ordenação sobre todos os documentos em memória, renderizando no DOM apenas a página ativa (`50 / 100 / 250 / Todas`, padrão `100`) nas Abas 1 e 2 com reset automático para a página 1 ao filtrar ou ordenar.

## Fase 4 — NFS-e pelo ADN (Ambiente de Dados Nacional)

- [x] **27. Busca automática de NFS-e pelo ADN Contribuintes: emitidas (prestador), tomadas (tomador) e intermediário**
  - Contrato confirmado no Swagger oficial e resumido em `docs/adn.md` (`GET /DFe/{NSU}`, `cnpjConsulta` sempre enviado, `404` = fim da fila, `TipoDocumento` NFSE/EVENTO guardados e DPS/CNC/demais contados e ignorados).
  - **Parte 1 (backend), esta entrega:** cliente REST com mTLS via `requests-pkcs12` (`sefaz/adn.py`), cursor de NSU próprio e persistido por página, limite de páginas e espera entre chamadas, trava por indisponibilidade/limite (sem tocar a rede), `sync_log` com origem `NFE`/`NFSE`, papel do CNPJ na nota, eventos de cancelamento, migração aditiva do SQLite (`origem`, `papel`), subcomando `nfe-local sincronizar-adn` (`--demo`), `POST /api/recebidas/sincronizar-adn` e filtros `origem`, `papel` e `tipo` em `GET /api/recebidas`.
  - **Correções e robustez posteriores (uso real):** `404` só é "fila zerada" com o JSON esperado (senão erro de endereço); `NFE_ADN_URL_BASE`/`--url-base`; `--diagnostico` somente leitura (com causa da falha de conexão, versão do TLS e `--tentativas`/`--timeout`); repetição automática só em falha de conexão (`--tentativas`, `NFE_ADN_TENTATIVAS`), com o total de tentativas e as falhas no `sync_log`.
  - **Parte 2 (interface e documentação), concluída:** aba **Buscar notas (SEFAZ e Portal Nacional)** (botões *Buscar NF-e de produto (SEFAZ)*, *Buscar NFS-e de serviço (Portal Nacional)*, *Buscar tudo* e *Diagnóstico do ADN*; seletor e cartões por tipo (produto, serviço, consumidor); somas sem canceladas nem eventos, por tipo e papel; lista de XMLs ilegíveis; cartões de status por origem; tabela com Tipo, Origem e Papel e filtros rápidos *Só emitidas*/*Só tomadas*); escopo **Tudo (acervo + SEFAZ + ADN)** deduplicado por chave, com coluna Origem e KPIs/resumo contando NFS-e; espelho DANFSe e XML para NFS-e do ADN; passo a passo quando não há certificado; demonstração com NFS-e sintéticas (sem tocar no cursor); endpoint `POST /api/recebidas/adn/diagnostico`; READMEs PT/EN com tabela de escopo, estado validado, política de espera/repetição e decisões; prints regenerados por `scripts/capturar_prints.py` com checagem de privacidade.
  - **Robustez da busca de NF-e (uso real):** a 1ª consulta em produção deu *Read timed out* em 30 s; o cliente do `NFeDistribuicaoDFe` passou a ter tempo limite de 90 s (`NFE_SEFAZ_TIMEOUT`, `--timeout`, 1 a 180 s) e repetição só em falha de conexão (3 tentativas, 5/15/30 s; `NFE_SEFAZ_TENTATIVAS`, `--tentativas`), sem tocar o `656`, o `137` nem o encerramento em `ultNSU == maxNSU`, e sem avançar o cursor em página que falhou.
  - **Validação em uso real (19/11/2026):** diagnóstico nos dois ambientes, uma página (50 NFS-e) e a sincronização **completa** em produção (10 páginas, fila zerada, 411 documentos: 391 NFS-e novas e 20 eventos, sem erro de processamento). O papel de intermediário vem de leitura do XML não confirmada no Swagger.

- [ ] **28. (Futuro) Avaliar substituir o `requests-pkcs12` por mTLS sem arquivo temporário**
  - **Problema:** o `Pkcs12Adapter` grava, a cada sessão HTTP, um arquivo temporário na pasta temporária do sistema com a chave privada em PEM PKCS#8 cifrada por senha aleatória e o apaga em seguida (`os.remove`, sem sobrescrita). A chave nunca fica em texto puro, mas toca o disco por um instante e pode deixar resíduo cifrado. O módulo `ssl` da biblioteca padrão só carrega cadeia de certificado a partir de arquivo (`load_cert_chain`), por isso a biblioteca faz isso.
  - **Opção a avaliar:** `pyOpenSSL` (`SSL.Context.use_privatekey` / `use_certificate` / `add_extra_chain_cert` com objetos em memória) integrado ao `urllib3`/`requests`.
  - **Prós:** elimina o arquivo temporário (a chave só existe em memória); mais controle sobre o contexto TLS (versão mínima, cifradores, informações da conexão para o diagnóstico); reduz a dependência de uma biblioteca pequena.
  - **Contras / riscos:** dependência extra (`pyOpenSSL`) e seu ciclo de atualizações; a integração com `urllib3` 2.x e `requests` precisa ser verificada (a injeção do pyOpenSSL no urllib3 é um caminho menos usado e pode estar descontinuada); mais código nosso para manter nos dois clientes (`dist_dfe.py` e `adn.py`); risco de regressão no handshake com o ADN e a SEFAZ, já confirmados em uso real (ver `docs/adn.md`), exigindo reteste real; os testes atuais usam sessões simuladas e não cobrem o handshake.
  - **Mitigação intermediária (sem trocar a biblioteca):** apontar a pasta temporária do processo (`TMPDIR`/`TEMP`) para uma pasta privada do usuário, fora de pastas sincronizadas, durante a criação da sessão; criar a sessão uma vez por execução em vez de por consulta.
  - **Critério de pronto:** diagnóstico e sincronização reais funcionando nos dois ambientes sem nenhum arquivo temporário criado (verificado monitorando a pasta temporária durante a chamada).
