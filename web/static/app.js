(() => {
  "use strict";

  const estado = {
    loteId: null,
    resumo: null,
    resultados: [],
  };

  const ROTULOS_ALERTA = {
    AMBIENTE_HOMOLOGACAO: "HOMOLOGAÇÃO",
    NOTA_SEM_PROTOCOLO: "SEM PROTOCOLO",
    SOMA_ITENS_DIVERGENTE: "SOMA DIVERGENTE",
    EQUACAO_VNF_DIVERGENTE: "TOTAL vNF DIVERGENTE",
    CHAVE_DUPLICADA_LOTE: "CHAVE DUPLICADA",
    CHAVE_TAMANHO_INVALIDO: "CHAVE INVÁLIDA",
    CHAVE_DV_INVALIDO: "DV CHAVE INVÁLIDO",
    DOCUMENTO_PARTE_INVALIDO: "CNPJ/CPF INVÁLIDO",
    LAYOUT_NFSE_MUNICIPAL_DESCONTINUADO: "NFS-e LEGADA",
    LAYOUT_DESCONHECIDO: "LAYOUT DESCONHECIDO",
    XML_MALFORMADO: "XML MALFORMADO",
    ZIP_INVALIDO: "ZIP INVÁLIDO",
  };

  const fmtMoeda = (valor) => {
    const num = Number(valor || 0);
    return num.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  const fmtMoedaRS = (valor) => `R$ ${fmtMoeda(valor)}`;

  const fmtDoc = (doc) => {
    if (!doc) return "-";
    const limpo = String(doc).replace(/[^0-9A-Za-z]/g, "").toUpperCase();
    if (limpo.length === 14) {
      return `${limpo.slice(0, 2)}.${limpo.slice(2, 5)}.${limpo.slice(5, 8)}/${limpo.slice(8, 12)}-${limpo.slice(12)}`;
    }
    if (limpo.length === 11 && /^\d+$/.test(limpo)) {
      return `${limpo.slice(0, 3)}.${limpo.slice(3, 6)}.${limpo.slice(6, 9)}-${limpo.slice(9)}`;
    }
    return limpo;
  };

  const escaparHtml = (str) =>
    String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const definirStatusUpload = (msg, erro = false) => {
    const el = document.getElementById("upload-status");
    if (!el) return;
    el.textContent = msg;
    el.style.color = erro ? "var(--status-err)" : "var(--accent-gold)";
  };

  const renderizarLote = (dados) => {
    estado.loteId = dados.lote_id;
    estado.resumo = dados.resumo;
    estado.resultados = dados.resultados || [];

    document.getElementById("painel-lote").classList.remove("hidden");
    document.getElementById("lbl-lote-id").textContent = `#${dados.lote_id}`;
    const dt = new Date(dados.criado_em);
    document.getElementById("lbl-criado-em").textContent = `Processado em ${dt.toLocaleString("pt-BR")}`;

    document.getElementById("link-export-xlsx").href = `/api/lote/${dados.lote_id}/exportar?formato=xlsx`;
    document.getElementById("link-export-csv").href = `/api/lote/${dados.lote_id}/exportar?formato=csv`;
    document.getElementById("link-export-itens-csv").href = `/api/lote/${dados.lote_id}/exportar?formato=itens_csv`;

    const c = dados.resumo.contagem;
    const imp = dados.resumo.impostos_totais;

    document.getElementById("kpi-total-arquivos").textContent = c.total_arquivos;
    const tipos = Object.entries(c.por_tipo || {})
      .map(([k, v]) => `${k}: ${v}`)
      .join(" | ");
    document.getElementById("kpi-por-tipo").textContent = tipos || "Nenhum documento";

    document.getElementById("kpi-validos").textContent = c.validos;
    document.getElementById("kpi-com-avisos").textContent = `${c.com_avisos} com avisos`;
    document.getElementById("kpi-com-erros").textContent = c.com_erros;
    document.getElementById("kpi-erros-fatais").textContent = `${c.erros_fatais_xml} ilegíveis/rejeitados`;

    document.getElementById("kpi-valor-total").textContent = fmtMoedaRS(dados.resumo.valor_total_notas);
    document.getElementById("kpi-valor-liquido").textContent = `Líquido: ${fmtMoedaRS(dados.resumo.valor_liquido_total)}`;

    document.getElementById("tax-icms").textContent = fmtMoedaRS(imp.icms);
    document.getElementById("tax-icms-st").textContent = fmtMoedaRS(imp.icms_st);
    document.getElementById("tax-ipi").textContent = fmtMoedaRS(imp.ipi);
    document.getElementById("tax-pis").textContent = fmtMoedaRS(imp.pis);
    document.getElementById("tax-cofins").textContent = fmtMoedaRS(imp.cofins);
    document.getElementById("tax-iss").textContent = fmtMoedaRS(imp.iss);
    document.getElementById("tax-retencoes").textContent = fmtMoedaRS(imp.retencoes_federais);
    document.getElementById("tax-ibs-cbs").textContent = fmtMoedaRS(imp.ibs_cbs);

    // Emitentes
    const tbodyEmit = document.getElementById("tbody-emitentes");
    tbodyEmit.innerHTML = (dados.resumo.por_emitente || [])
      .map((em) => {
        const somaImp =
          Number(em.impostos.icms || 0) +
          Number(em.impostos.ipi || 0) +
          Number(em.impostos.iss || 0);
        return `
          <tr>
            <td><code>${escaparHtml(fmtDoc(em.documento))}</code></td>
            <td>${escaparHtml(em.razao_social || "-")}</td>
            <td>${escaparHtml(em.uf || "-")}</td>
            <td class="num">${em.quantidade_notas}</td>
            <td class="num"><strong>${fmtMoeda(em.valor_total_notas)}</strong></td>
            <td class="num">${fmtMoeda(somaImp)}</td>
          </tr>
        `;
      })
      .join("");

    // Períodos
    const tbodyPer = document.getElementById("tbody-periodos");
    tbodyPer.innerHTML = (dados.resumo.por_mes || [])
      .map(
        (p) => `
          <tr>
            <td><code>${escaparHtml(p.periodo)}</code></td>
            <td class="num">${p.quantidade_notas}</td>
            <td class="num">${fmtMoeda(p.valor_produtos_servicos)}</td>
            <td class="num"><strong>${fmtMoeda(p.valor_total_notas)}</strong></td>
            <td class="num">${fmtMoeda(p.impostos.ibs_cbs)}</td>
          </tr>
        `
      )
      .join("");

    const boxRespLlm = document.getElementById("llm-resposta-box");
    if (boxRespLlm) boxRespLlm.classList.add("hidden");
    atualizarStatusLLM();

    renderizarTabelaNotas();
  };

  const atualizarStatusLLM = async () => {
    const badge = document.getElementById("llm-status-badge");
    const quota = document.getElementById("llm-quota-counter");
    if (!badge || !quota) return;

    try {
      const resp = await fetch("/api/llm/status");
      if (!resp.ok) return;
      const st = await resp.json();
      if (st.configurado) {
        badge.className = "badge badge-ok";
        badge.textContent = `Ativo (${st.modelo})`;
      } else {
        badge.className = "badge badge-warn";
        badge.textContent = "Desativado (defina LLM_API_KEY no .env)";
      }
      quota.textContent = `Chamadas restantes: ${st.chamadas_restantes}/${st.max_chamadas_hora} por hora`;
    } catch (_) {
      badge.className = "badge badge-type";
      badge.textContent = "Status indisponível";
    }
  };

  const enviarPerguntaLLM = async (perguntaTexto) => {
    if (!estado.loteId) return;
    const pergunta = (perguntaTexto || "").trim();
    if (pergunta.length < 3) return;

    const btn = document.getElementById("btn-enviar-pergunta-llm");
    const box = document.getElementById("llm-resposta-box");
    const modeloEl = document.getElementById("llm-resposta-modelo");
    const cacheBadge = document.getElementById("llm-badge-cache");
    const textoEl = document.getElementById("llm-resposta-texto");
    const quotaEl = document.getElementById("llm-quota-counter");

    btn.disabled = true;
    box.classList.remove("hidden");
    cacheBadge.classList.add("hidden");
    modeloEl.textContent = "Consultando assistente...";
    textoEl.textContent = "Aguarde...";

    try {
      const resp = await fetch(`/api/lote/${estado.loteId}/perguntar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pergunta }),
      });
      const dados = await resp.json();
      if (!resp.ok) {
        modeloEl.textContent = `Aviso (${resp.status})`;
        textoEl.textContent = dados.detail || "Não foi possível consultar o assistente LLM.";
        return;
      }

      modeloEl.textContent = `Modelo: ${dados.modelo}`;
      if (dados.em_cache) {
        cacheBadge.classList.remove("hidden");
      } else {
        cacheBadge.classList.add("hidden");
      }
      textoEl.textContent = dados.resposta;
      if (quotaEl && typeof dados.chamadas_restantes === "number") {
        quotaEl.textContent = `Chamadas restantes: ${dados.chamadas_restantes} na hora atual`;
      }
    } catch (err) {
      modeloEl.textContent = "Erro de comunicação";
      textoEl.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  };

  const renderizarBadgesNota = (res) => {
    const badges = [];
    if (res.valido && (!res.alertas || res.alertas.length === 0)) {
      badges.push('<span class="badge badge-ok">VÁLIDA</span>');
    } else if (res.valido) {
      badges.push('<span class="badge badge-ok">VÁLIDA COM AVISO</span>');
    } else {
      badges.push('<span class="badge badge-err">INVÁLIDA</span>');
    }

    for (const alerta of res.alertas || []) {
      const classe = alerta.severidade === "ERRO" ? "badge-err" : "badge-warn";
      const rotulo = ROTULOS_ALERTA[alerta.codigo] || alerta.codigo;
      badges.push(
        `<span class="badge ${classe}" title="${escaparHtml(alerta.mensagem)}">${escaparHtml(rotulo)}</span>`
      );
    }
    return badges.join(" ");
  };

  const renderizarTabelaNotas = () => {
    const termo = (document.getElementById("filtro-busca").value || "").trim().toLowerCase();
    const filtroTipo = document.getElementById("filtro-tipo").value;
    const filtroStatus = document.getElementById("filtro-status").value;

    const tbody = document.getElementById("tbody-notas");
    const linhas = [];

    estado.resultados.forEach((res, idx) => {
      const doc = res.documento;
      const tipo = doc ? doc.tipo : "ERRO_XML";

      if (filtroTipo && tipo !== filtroTipo) return;
      if (filtroStatus === "validos" && !res.valido) return;
      if (filtroStatus === "erros" && res.valido) return;
      if (
        filtroStatus === "avisos" &&
        !(res.alertas || []).some((a) => a.severidade === "AVISO")
      ) {
        return;
      }

      if (termo) {
        const alvo = [
          res.arquivo_origem,
          doc?.chave_acesso,
          doc?.numero,
          doc?.emitente?.documento,
          doc?.emitente?.razao_social,
          doc?.destinatario?.documento,
          doc?.destinatario?.razao_social,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!alvo.includes(termo)) return;
      }

      const numSerie = doc ? `${doc.numero || "-"} / ${doc.serie || "-"}` : "-";
      const emitenteStr = doc
        ? `<strong>${escaparHtml(doc.emitente.razao_social || "-")}</strong><br/><span class="muted">${escaparHtml(fmtDoc(doc.emitente.documento))}</span>`
        : '<span class="muted">-</span>';
      const destStr =
        doc && doc.destinatario
          ? `${escaparHtml(doc.destinatario.razao_social || "-")}<br/><span class="muted">${escaparHtml(fmtDoc(doc.destinatario.documento))}</span>`
          : '<span class="muted">Não identificado</span>';
      const valorTotal = doc ? fmtMoeda(doc.totais.valor_total_nota) : "0,00";

      const rotuloEspelho = tipo === "NFSE" ? "Espelho DANFSe" : "Espelho DANFE";
      const btnEspelho = doc
        ? `<a class="btn btn-sm btn-primary" href="/lote/${estado.loteId}/nota/${idx}/espelho" target="_blank" rel="noopener">${rotuloEspelho}</a>`
        : "";

      linhas.push(`
        <tr>
          <td>
            <strong>${escaparHtml(res.arquivo_origem)}</strong>
            ${doc?.chave_acesso ? `<br/><code class="muted">${escaparHtml(doc.chave_acesso)}</code>` : ""}
          </td>
          <td><span class="badge badge-type">${escaparHtml(tipo)}</span></td>
          <td>${escaparHtml(numSerie)}</td>
          <td>${emitenteStr}</td>
          <td>${destStr}</td>
          <td class="num"><strong>${valorTotal}</strong></td>
          <td>${renderizarBadgesNota(res)}</td>
          <td>
            <div class="row-actions">
              ${btnEspelho}
              <button type="button" class="btn btn-sm btn-secondary" data-action="detalhes" data-idx="${idx}">
                Alertas / Detalhes
              </button>
              <a class="btn btn-sm btn-ghost" href="/lote/${estado.loteId}/nota/${idx}/xml" download>
                Baixar XML
              </a>
            </div>
          </td>
        </tr>
      `);
    });

    tbody.innerHTML =
      linhas.join("") ||
      '<tr><td colspan="8" style="text-align:center;" class="muted">Nenhum documento corresponde aos filtros aplicados.</td></tr>';
  };

  const abrirModalDetalhes = (idx) => {
    const res = estado.resultados[idx];
    if (!res) return;

    const modal = document.getElementById("modal-detalhes");
    const corpo = document.getElementById("modal-conteudo");
    document.getElementById("modal-titulo").textContent = `Diagnóstico Fiscal — ${res.arquivo_origem}`;

    const alertasHtml =
      (res.alertas || []).length > 0
        ? `<ul>${res.alertas
            .map(
              (a) =>
                `<li><strong>[${escaparHtml(a.severidade)}] ${escaparHtml(a.codigo)}:</strong> ${escaparHtml(a.mensagem)}</li>`
            )
            .join("")}</ul>`
        : "<p>Nenhum erro ou aviso apontado pelos validadores.</p>";

    corpo.innerHTML = `
      <div style="margin-bottom: 1rem;">
        <h4>Alertas de Validação</h4>
        ${alertasHtml}
      </div>
      <div>
        <h4>Estrutura Normalizada (JSON)</h4>
        <pre>${escaparHtml(JSON.stringify(res, null, 2))}</pre>
      </div>
    `;
    modal.classList.remove("hidden");
  };

  const enviarArquivos = async (fileList) => {
    if (!fileList || fileList.length === 0) return;
    definirStatusUpload(`Processando ${fileList.length} arquivo(s)...`);

    const formData = new FormData();
    Array.from(fileList).forEach((f) => formData.append("files", f));

    try {
      const resp = await fetch("/api/lote", { method: "POST", body: formData });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusUpload(dados.detail || "Falha ao processar lote.", true);
        return;
      }
      definirStatusUpload(`Lote #${dados.lote_id} processado com sucesso!`);
      renderizarLote(dados);
    } catch (err) {
      definirStatusUpload(`Erro de comunicação: ${err.message}`, true);
    }
  };

  const carregarLoteDemo = async () => {
    definirStatusUpload("Carregando lote de demonstração sintético...");
    try {
      const resp = await fetch("/api/lote/demo", { method: "POST" });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusUpload(dados.detail || "Falha ao carregar demonstração.", true);
        return;
      }
      definirStatusUpload(`Lote de demonstração #${dados.lote_id} carregado!`);
      renderizarLote(dados);
    } catch (err) {
      definirStatusUpload(`Erro ao carregar demonstração: ${err.message}`, true);
    }
  };

  // --- Fase 2: Recebidas da SEFAZ (NFeDistribuicaoDFe) ---
  const definirStatusRecebidas = (msg, erro = false) => {
    const el = document.getElementById("recebidas-sync-status");
    if (!el) return;
    el.textContent = msg;
    el.style.color = erro ? "var(--status-err)" : "var(--accent-gold)";
  };

  const alternarAba = (nomeAba) => {
    const abaLotes = document.getElementById("aba-lotes");
    const abaRecebidas = document.getElementById("aba-recebidas");
    const btnLotes = document.getElementById("btn-tab-lotes");
    const btnRecebidas = document.getElementById("btn-tab-recebidas");
    if (!abaLotes || !abaRecebidas || !btnLotes || !btnRecebidas) return;

    if (nomeAba === "recebidas") {
      abaLotes.classList.add("hidden");
      abaRecebidas.classList.remove("hidden");
      btnLotes.classList.remove("active");
      btnRecebidas.classList.add("active");
      carregarStatusRecebidas();
      carregarListaRecebidas();
    } else {
      abaRecebidas.classList.add("hidden");
      abaLotes.classList.remove("hidden");
      btnRecebidas.classList.remove("active");
      btnLotes.classList.add("active");
    }
  };

  const carregarStatusRecebidas = async () => {
    const badgeCert = document.getElementById("cert-badge-status");
    const detalhesCert = document.getElementById("cert-detalhes");
    const badgeAmb = document.getElementById("nsu-badge-ambiente");
    const lblUltNsu = document.getElementById("lbl-ult-nsu");
    const lblMaxNsu = document.getElementById("lbl-max-nsu");
    const lblUltRet = document.getElementById("lbl-ultimo-retorno");
    const bannerBloqueio = document.getElementById("banner-bloqueio-656");
    const btnSync = document.getElementById("btn-sync-sefaz");

    if (!badgeCert || !detalhesCert) return;

    try {
      const resp = await fetch("/api/recebidas/status");
      if (!resp.ok) return;
      const st = await resp.json();

      if (st.certificado_configurado && st.certificado) {
        const c = st.certificado;
        if (c.vencido) {
          badgeCert.className = "badge badge-err";
          badgeCert.textContent = "Certificado Vencido";
        } else {
          badgeCert.className = "badge badge-ok";
          badgeCert.textContent = `Configurado (${c.dias_para_vencer} dias restantes)`;
        }
        const dtValidade = c.valido_ate ? new Date(c.valido_ate).toLocaleDateString("pt-BR") : "-";
        detalhesCert.innerHTML = `
          <div><strong>Titular:</strong> ${escaparHtml(c.titular)}</div>
          <div><strong>CNPJ:</strong> <code>${escaparHtml(fmtDoc(c.documento))}</code> | <strong>Válido até:</strong> ${escaparHtml(dtValidade)}</div>
        `;
      } else {
        badgeCert.className = "badge badge-warn";
        badgeCert.textContent = "Não Configurado";
        detalhesCert.innerHTML = `
          <div>${escaparHtml(st.erro_certificado || "Configure NFE_CERT_PATH e NFE_CERT_SENHA no arquivo .env para consultar a SEFAZ.")}</div>
          <div class="muted" style="margin-top:0.2rem;">Dica: use o botão <strong>"🧪 Carregar Recebidas de Demonstração"</strong> para testar os filtros sem certificado.</div>
        `;
      }

      if (badgeAmb) {
        badgeAmb.textContent = st.ambiente === 1 ? "Produção (1)" : "Homologação (2)";
      }
      if (lblUltNsu && st.cursor) lblUltNsu.textContent = st.cursor.ultimo_nsu;
      if (lblMaxNsu && st.cursor) lblMaxNsu.textContent = st.cursor.max_nsu;

      if (lblUltRet && st.cursor) {
        if (st.cursor.ultimo_cstat) {
          const dtCons = st.cursor.ultima_consulta
            ? new Date(st.cursor.ultima_consulta).toLocaleString("pt-BR")
            : "-";
          lblUltRet.textContent = `Último retorno: cStat ${st.cursor.ultimo_cstat} (${st.cursor.ultimo_motivo || "-"}) em ${dtCons}`;
        } else {
          lblUltRet.textContent = "Nenhuma consulta realizada ainda neste ambiente.";
        }
      }

      if (bannerBloqueio) {
        if (st.bloqueado) {
          const minRest = Math.max(1, Math.ceil((st.segundos_bloqueio_restantes || 0) / 60));
          const dtAte = st.cursor?.bloqueado_ate
            ? new Date(st.cursor.bloqueado_ate).toLocaleTimeString("pt-BR")
            : "";
          bannerBloqueio.classList.remove("hidden");
          bannerBloqueio.innerHTML = `
            ⛔ <strong>Trava obrigatória SEFAZ ativa (cStat 656 — Consumo Indevido):</strong>
            aguarde ~${minRest} min (até ${escaparHtml(dtAte)}) antes de sincronizar novamente para não reiniciar o bloqueio de 1 hora na SEFAZ.
          `;
          if (btnSync) btnSync.disabled = true;
        } else {
          bannerBloqueio.classList.add("hidden");
          if (btnSync) btnSync.disabled = false;
        }
      }
    } catch (_) {
      badgeCert.className = "badge badge-type";
      badgeCert.textContent = "Indisponível";
    }
  };

  const renderizarBadgesRecebida = (nota) => {
    const badges = [];
    if (nota.situacao === "AUTORIZADA") {
      badges.push('<span class="badge badge-ok">AUTORIZADA</span>');
    } else if (nota.situacao === "CANCELADA") {
      badges.push('<span class="badge badge-err">CANCELADA</span>');
    } else if (nota.situacao === "RESUMO") {
      badges.push('<span class="badge badge-warn">RESUMO (resNFe)</span>');
    } else {
      badges.push(`<span class="badge badge-type">${escaparHtml(nota.situacao)}</span>`);
    }

    if (nota.manifestacao_necessaria) {
      badges.push(
        '<span class="badge badge-warn" title="Registre Ciência da Operação (210210) para baixar o procNFe completo">⚠️ Exige Manifestação (210210)</span>'
      );
    }
    return badges.join(" ");
  };

  const carregarListaRecebidas = async () => {
    const tbody = document.getElementById("tbody-recebidas");
    if (!tbody) return;

    const params = new URLSearchParams();
    const emitente = (document.getElementById("rec-filtro-emitente")?.value || "").trim();
    const dtInicio = (document.getElementById("rec-filtro-data-inicio")?.value || "").trim();
    const dtFim = (document.getElementById("rec-filtro-data-fim")?.value || "").trim();
    const vMin = (document.getElementById("rec-filtro-valor-min")?.value || "").trim();
    const vMax = (document.getElementById("rec-filtro-valor-max")?.value || "").trim();
    const categoria = (document.getElementById("rec-filtro-categoria")?.value || "").trim();
    const situacao = (document.getElementById("rec-filtro-situacao")?.value || "").trim();

    if (emitente) params.set("emitente", emitente);
    if (dtInicio) params.set("data_inicio", dtInicio);
    if (dtFim) params.set("data_fim", dtFim);
    if (vMin) params.set("valor_min", vMin);
    if (vMax) params.set("valor_max", vMax);
    if (categoria) params.set("categoria", categoria);
    if (situacao) params.set("situacao", situacao);

    const qs = params.toString();
    const url = qs ? `/api/recebidas?${qs}` : "/api/recebidas";

    try {
      const resp = await fetch(url);
      if (!resp.ok) return;
      const dados = await resp.json();

      const kpiTotal = document.getElementById("rec-kpi-total");
      const kpiValor = document.getElementById("rec-kpi-valor");
      if (kpiTotal) kpiTotal.textContent = String(dados.total || 0);
      if (kpiValor) kpiValor.textContent = fmtMoedaRS(dados.valor_total_filtrado || 0);

      const notas = dados.notas || [];
      if (notas.length === 0) {
        tbody.innerHTML =
          '<tr><td colspan="7" style="text-align:center;" class="muted">Nenhuma nota recebida encontrada para os filtros selecionados.</td></tr>';
        return;
      }

      tbody.innerHTML = notas
        .map((n) => {
          const dtEmi = n.data_emissao
            ? new Date(n.data_emissao).toLocaleDateString("pt-BR")
            : "-";
          const numSerie = n.numero ? `${n.numero} / ${n.serie || "-"}` : "Resumo s/ número";
          const chaveUrl = encodeURIComponent(n.chave);
          return `
            <tr>
              <td><code>${escaparHtml(n.nsu)}</code></td>
              <td>${escaparHtml(dtEmi)}</td>
              <td>
                <strong>${escaparHtml(numSerie)}</strong>
                <br/><code class="muted">${escaparHtml(n.chave)}</code>
              </td>
              <td>
                <strong>${escaparHtml(n.emitente_nome || "-")}</strong>
                <br/><span class="muted">${escaparHtml(fmtDoc(n.emitente_documento))}</span>
              </td>
              <td class="num"><strong>${fmtMoeda(n.valor_total)}</strong></td>
              <td>${renderizarBadgesRecebida(n)}</td>
              <td>
                <div class="row-actions">
                  <a class="btn btn-sm btn-primary" href="/recebidas/${chaveUrl}/espelho" target="_blank" rel="noopener">
                    Espelho DANFE
                  </a>
                  <a class="btn btn-sm btn-ghost" href="/recebidas/${chaveUrl}/xml" download>
                    Baixar XML
                  </a>
                </div>
              </td>
            </tr>
          `;
        })
        .join("");
    } catch (err) {
      definirStatusRecebidas(`Erro ao listar notas recebidas: ${err.message}`, true);
    }
  };

  const sincronizarComSefaz = async () => {
    definirStatusRecebidas("Sincronizando com o Ambiente Nacional da SEFAZ (NFeDistribuicaoDFe)...");
    try {
      const resp = await fetch("/api/recebidas/sincronizar", { method: "POST" });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusRecebidas(dados.detail || `Falha na sincronização (HTTP ${resp.status}).`, true);
        await carregarStatusRecebidas();
        return;
      }
      definirStatusRecebidas(
        `Sincronização concluída: ${dados.paginas_consultadas} página(s), ${dados.novas_notas} nova(s), ${dados.notas_atualizadas} atualizada(s), ${dados.eventos_registrados} evento(s).`
      );
      await carregarStatusRecebidas();
      await carregarListaRecebidas();
    } catch (err) {
      definirStatusRecebidas(`Erro de comunicação: ${err.message}`, true);
    }
  };

  const carregarRecebidasDemo = async () => {
    definirStatusRecebidas("Carregando lote sintético DistDFe no SQLite local...");
    try {
      const resp = await fetch("/api/recebidas/demo", { method: "POST" });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusRecebidas(dados.detail || "Falha ao carregar demonstração DistDFe.", true);
        return;
      }
      definirStatusRecebidas(
        `Demonstração DistDFe carregada (${dados.documentos_processados} docZip processados, ultNSU=${dados.cursor.ultimo_nsu}).`
      );
      await carregarStatusRecebidas();
      await carregarListaRecebidas();
    } catch (err) {
      definirStatusRecebidas(`Erro ao carregar demonstração: ${err.message}`, true);
    }
  };

  const limparFiltrosRecebidas = () => {
    [
      "rec-filtro-emitente",
      "rec-filtro-data-inicio",
      "rec-filtro-data-fim",
      "rec-filtro-valor-min",
      "rec-filtro-valor-max",
      "rec-filtro-categoria",
      "rec-filtro-situacao",
    ].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
    carregarListaRecebidas();
  };

  document.addEventListener("DOMContentLoaded", () => {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("file-input");

    const btnTabLotes = document.getElementById("btn-tab-lotes");
    const btnTabRecebidas = document.getElementById("btn-tab-recebidas");
    if (btnTabLotes && btnTabRecebidas) {
      btnTabLotes.addEventListener("click", () => alternarAba("lotes"));
      btnTabRecebidas.addEventListener("click", () => alternarAba("recebidas"));
    }

    const btnSyncSefaz = document.getElementById("btn-sync-sefaz");
    if (btnSyncSefaz) btnSyncSefaz.addEventListener("click", sincronizarComSefaz);

    const btnDemoRec = document.getElementById("btn-demo-recebidas");
    if (btnDemoRec) btnDemoRec.addEventListener("click", carregarRecebidasDemo);

    const formFiltrosRec = document.getElementById("form-filtros-recebidas");
    if (formFiltrosRec) {
      formFiltrosRec.addEventListener("submit", (e) => {
        e.preventDefault();
        carregarListaRecebidas();
      });
    }

    const btnLimparRec = document.getElementById("btn-rec-limpar");
    if (btnLimparRec) btnLimparRec.addEventListener("click", limparFiltrosRecebidas);

    document.getElementById("btn-select-files").addEventListener("click", (e) => {
      e.stopPropagation();
      fileInput.click();
    });

    dropzone.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      fileInput.click();
    });

    fileInput.addEventListener("change", () => enviarArquivos(fileInput.files));

    ["dragenter", "dragover"].forEach((evName) => {
      dropzone.addEventListener(evName, (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((evName) => {
      dropzone.addEventListener(evName, (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
      });
    });

    dropzone.addEventListener("drop", (e) => {
      if (e.dataTransfer?.files?.length) {
        enviarArquivos(e.dataTransfer.files);
      }
    });

    document.getElementById("btn-demo").addEventListener("click", carregarLoteDemo);
    document.getElementById("btn-demo-inline").addEventListener("click", (e) => {
      e.stopPropagation();
      carregarLoteDemo();
    });

    document.getElementById("btn-theme").addEventListener("click", () => {
      const html = document.documentElement;
      const atual = html.getAttribute("data-theme") || "dark";
      html.setAttribute("data-theme", atual === "dark" ? "light" : "dark");
    });

    ["filtro-busca", "filtro-tipo", "filtro-status"].forEach((id) => {
      document.getElementById(id).addEventListener("input", renderizarTabelaNotas);
      document.getElementById(id).addEventListener("change", renderizarTabelaNotas);
    });

    document.getElementById("tbody-notas").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action='detalhes']");
      if (btn) {
        abrirModalDetalhes(Number(btn.dataset.idx));
      }
    });

    document.getElementById("btn-fechar-modal").addEventListener("click", () => {
      document.getElementById("modal-detalhes").classList.add("hidden");
    });

    const formPergunta = document.getElementById("form-pergunta-llm");
    const inputPergunta = document.getElementById("input-pergunta-llm");
    if (formPergunta && inputPergunta) {
      formPergunta.addEventListener("submit", (e) => {
        e.preventDefault();
        enviarPerguntaLLM(inputPergunta.value);
      });
    }

    document.querySelectorAll(".llm-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const p = chip.getAttribute("data-pergunta") || "";
        if (inputPergunta) inputPergunta.value = p;
        enviarPerguntaLLM(p);
      });
    });

    atualizarStatusLLM();
  });
})();
