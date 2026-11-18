(() => {
  "use strict";

  const estado = {
    escopoId: "acervo",
    loteId: null,
    resumo: null,
    resultados: [],
    historicoLotes: [],
    subabaAtiva: "notas",
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
    MANIFESTACAO_DESTINATARIO_NECESSARIA: "EXIGE MANIFESTAÇÃO",
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

  const alternarSubaba = (nomeSubaba) => {
    estado.subabaAtiva = nomeSubaba;
    const mapa = {
      notas: ["btn-subtab-notas", "subview-notas"],
      resumo: ["btn-subtab-resumo", "subview-resumo"],
      ia: ["btn-subtab-ia", "subview-ia"],
    };
    Object.entries(mapa).forEach(([chave, [btnId, viewId]]) => {
      const btn = document.getElementById(btnId);
      const view = document.getElementById(viewId);
      if (!btn || !view) return;
      if (chave === nomeSubaba) {
        btn.classList.add("active");
        view.classList.remove("hidden");
      } else {
        btn.classList.remove("active");
        view.classList.add("hidden");
      }
    });
  };

  const popularSeletorEmitentes = (resultados) => {
    const select = document.getElementById("filtro-emitente");
    if (!select) return;
    const valorAtual = select.value;
    const mapaEmitentes = new Map();

    (resultados || []).forEach((res) => {
      const em = res.documento?.emitente;
      if (!em) return;
      const chave = (em.documento || em.razao_social || "").trim();
      if (!chave) return;
      if (!mapaEmitentes.has(chave)) {
        const docFmt = em.documento ? fmtDoc(em.documento) : "";
        const nome = em.razao_social || docFmt;
        const rotulo = docFmt && em.razao_social ? `${em.razao_social} (${docFmt})` : nome;
        mapaEmitentes.set(chave, rotulo);
      }
    });

    const opcoes = ['<option value="">Todos os Emitentes</option>'];
    Array.from(mapaEmitentes.entries())
      .sort((a, b) => a[1].localeCompare(b[1], "pt-BR"))
      .forEach(([chave, rotulo]) => {
        opcoes.push(`<option value="${escaparHtml(chave)}">${escaparHtml(rotulo)}</option>`);
      });

    select.innerHTML = opcoes.join("");
    if (valorAtual && mapaEmitentes.has(valorAtual)) {
      select.value = valorAtual;
    }
  };

  const renderizarSidebarHistorico = () => {
    const listaEl = document.getElementById("lista-historico-lotes");
    const badgeTotal = document.getElementById("badge-total-lotes");
    const btnAcervo = document.getElementById("btn-escopo-acervo");
    const subAcervo = document.getElementById("lbl-acervo-sub");

    if (badgeTotal) {
      const qtd = estado.historicoLotes.length;
      badgeTotal.textContent = `${qtd} ${qtd === 1 ? "lote" : "lotes"}`;
    }

    if (btnAcervo) {
      if (estado.escopoId === "acervo") {
        btnAcervo.classList.add("active");
      } else {
        btnAcervo.classList.remove("active");
      }
    }

    if (subAcervo) {
      if (estado.escopoId === "acervo" && estado.resumo) {
        const c = estado.resumo.contagem;
        subAcervo.textContent = `${c.total_arquivos} doc(s) únicos • ${fmtMoedaRS(estado.resumo.valor_total_notas)}`;
      } else {
        const somaArqs = estado.historicoLotes.reduce((acc, l) => acc + (l.total_arquivos || 0), 0);
        subAcervo.textContent =
          somaArqs > 0
            ? `${estado.historicoLotes.length} lote(s) salvos no SQLite`
            : "Todas as notas consolidadas sem duplicar chaves";
      }
    }

    if (!listaEl) return;
    if (estado.historicoLotes.length === 0) {
      listaEl.innerHTML = '<div class="history-empty muted">Nenhum lote importado ainda.</div>';
      return;
    }

    listaEl.innerHTML = estado.historicoLotes
      .map((item) => {
        const ativo = estado.escopoId === item.lote_id ? "active" : "";
        const badgeOrigem =
          item.origem === "demo"
            ? '<span class="badge badge-warn">DEMO</span>'
            : '<span class="badge badge-ok">UPLOAD</span>';
        const dt = item.criado_em ? new Date(item.criado_em).toLocaleString("pt-BR") : "-";
        return `
          <div class="history-item ${ativo}" role="listitem">
            <button type="button" class="history-item-main" data-action="selecionar-lote" data-lote-id="${escaparHtml(item.lote_id)}">
              <div class="history-item-title" title="${escaparHtml(item.nome_rotulo)}">
                ${escaparHtml(item.nome_rotulo)}
              </div>
              <div class="history-item-meta">
                ${badgeOrigem}
                <span>${item.total_arquivos} doc(s)</span>
                <span>•</span>
                <strong>${fmtMoedaRS(item.valor_total_notas)}</strong>
              </div>
              <div class="history-item-meta">
                <span>#${escaparHtml(item.lote_id)} • ${escaparHtml(dt)}</span>
              </div>
            </button>
            <button
              type="button"
              class="history-item-del"
              data-action="excluir-lote"
              data-lote-id="${escaparHtml(item.lote_id)}"
              title="Excluir este lote do SQLite"
            >
              🗑️
            </button>
          </div>
        `;
      })
      .join("");
  };

  const renderizarLote = (dados, rotuloPersonalizado = "") => {
    estado.loteId = dados.lote_id;
    estado.escopoId = dados.lote_id;
    estado.resumo = dados.resumo;
    estado.resultados = dados.resultados || [];

    const painelLote = document.getElementById("painel-lote");
    const estadoVazio = document.getElementById("estado-vazio-lotes");
    const temDados =
      (dados.resultados && dados.resultados.length > 0) || estado.historicoLotes.length > 0;

    if (temDados) {
      painelLote.classList.remove("hidden");
      if (estadoVazio) estadoVazio.classList.add("hidden");
    } else {
      painelLote.classList.add("hidden");
      if (estadoVazio) estadoVazio.classList.remove("hidden");
    }

    const ehAcervo = dados.lote_id === "acervo";
    const badgeEscopo = document.getElementById("badge-escopo-ativo");
    const tituloEscopo = document.getElementById("lbl-escopo-titulo");
    const lblLoteId = document.getElementById("lbl-lote-id");

    if (ehAcervo) {
      if (badgeEscopo) {
        badgeEscopo.className = "badge badge-gold";
        badgeEscopo.textContent = "ACERVO UNIFICADO";
      }
      if (tituloEscopo) tituloEscopo.textContent = "📚 Todo o Acervo Unificado";
      if (lblLoteId) lblLoteId.textContent = `(${estado.historicoLotes.length} lotes no SQLite)`;
    } else {
      const infoHist = estado.historicoLotes.find((l) => l.lote_id === dados.lote_id);
      const nomeLote = rotuloPersonalizado || infoHist?.nome_rotulo || "Lote Individual";
      if (badgeEscopo) {
        badgeEscopo.className = infoHist?.origem === "demo" ? "badge badge-warn" : "badge badge-ok";
        badgeEscopo.textContent = infoHist?.origem === "demo" ? "LOTE DEMO" : "LOTE INDIVIDUAL";
      }
      if (tituloEscopo) tituloEscopo.textContent = `📦 ${nomeLote}`;
      if (lblLoteId) lblLoteId.textContent = `#${dados.lote_id}`;
    }

    const dt = new Date(dados.criado_em);
    document.getElementById("lbl-criado-em").textContent = ehAcervo
      ? `Consolidado do banco SQLite local (última atualização em ${dt.toLocaleString("pt-BR")})`
      : `Processado em ${dt.toLocaleString("pt-BR")}`;

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

    // Emitentes (clicáveis para filtrar a aba Notas Fiscais!)
    const tbodyEmit = document.getElementById("tbody-emitentes");
    tbodyEmit.innerHTML =
      (dados.resumo.por_emitente || [])
        .map((em) => {
          const somaImp =
            Number(em.impostos.icms || 0) +
            Number(em.impostos.ipi || 0) +
            Number(em.impostos.iss || 0);
          const chaveEmit = em.documento || em.razao_social || "";
          return `
            <tr class="clickable-row" data-action="filtrar-emitente" data-emitente="${escaparHtml(chaveEmit)}" title="Clique para ver apenas as notas deste emitente">
              <td><code>${escaparHtml(fmtDoc(em.documento))}</code></td>
              <td><strong>${escaparHtml(em.razao_social || "-")}</strong></td>
              <td>${escaparHtml(em.uf || "-")}</td>
              <td class="num">${em.quantidade_notas}</td>
              <td class="num"><strong>${fmtMoeda(em.valor_total_notas)}</strong></td>
              <td class="num">${fmtMoeda(somaImp)}</td>
            </tr>
          `;
        })
        .join("") ||
      '<tr><td colspan="6" style="text-align:center;" class="muted">Nenhum emitente consolidado.</td></tr>';

    // Períodos
    const tbodyPer = document.getElementById("tbody-periodos");
    tbodyPer.innerHTML =
      (dados.resumo.por_mes || [])
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
        .join("") ||
      '<tr><td colspan="5" style="text-align:center;" class="muted">Nenhuma competência consolidada.</td></tr>';

    const boxRespLlm = document.getElementById("llm-resposta-box");
    if (boxRespLlm) boxRespLlm.classList.add("hidden");
    atualizarStatusLLM();

    popularSeletorEmitentes(estado.resultados);
    renderizarSidebarHistorico();
    renderizarTabelaNotas();
  };

  const carregarHistoricoLotes = async () => {
    try {
      const resp = await fetch("/api/lotes");
      if (!resp.ok) return [];
      const dados = await resp.json();
      estado.historicoLotes = dados.lotes || [];
      renderizarSidebarHistorico();
      return estado.historicoLotes;
    } catch (_) {
      return [];
    }
  };

  const carregarEscopo = async (escopoId = "acervo") => {
    try {
      const url = escopoId === "acervo" ? "/api/acervo" : `/api/lote/${encodeURIComponent(escopoId)}`;
      const resp = await fetch(url);
      if (!resp.ok) return;
      const dados = await resp.json();
      renderizarLote(dados);
    } catch (err) {
      definirStatusUpload(`Erro ao carregar escopo: ${err.message}`, true);
    }
  };

  const excluirLoteDoHistorico = async (loteId) => {
    try {
      const resp = await fetch(`/api/lote/${encodeURIComponent(loteId)}`, { method: "DELETE" });
      if (!resp.ok) return;
      await carregarHistoricoLotes();
      if (estado.historicoLotes.length === 0) {
        estado.loteId = null;
        estado.resultados = [];
        document.getElementById("painel-lote").classList.add("hidden");
        document.getElementById("estado-vazio-lotes")?.classList.remove("hidden");
        definirStatusUpload("Histórico vazio.");
      } else {
        await carregarEscopo("acervo");
        definirStatusUpload(`Lote #${loteId} removido do histórico.`);
      }
    } catch (err) {
      definirStatusUpload(`Erro ao excluir lote: ${err.message}`, true);
    }
  };

  const limparHistorico = async (apenasDemo = false) => {
    try {
      const qs = apenasDemo ? "?apenas_demo=true" : "";
      const resp = await fetch(`/api/lotes${qs}`, { method: "DELETE" });
      if (!resp.ok) return;
      const dados = await resp.json();
      await carregarHistoricoLotes();
      if (estado.historicoLotes.length === 0) {
        estado.loteId = null;
        estado.resultados = [];
        document.getElementById("painel-lote").classList.add("hidden");
        document.getElementById("estado-vazio-lotes")?.classList.remove("hidden");
      } else {
        await carregarEscopo("acervo");
      }
      definirStatusUpload(
        apenasDemo
          ? `${dados.removidos} lote(s) de demonstração removido(s).`
          : `Histórico zerado (${dados.removidos} lote(s) removido(s)).`
      );
    } catch (err) {
      definirStatusUpload(`Erro ao limpar histórico: ${err.message}`, true);
    }
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
    const termo = (document.getElementById("filtro-busca")?.value || "").trim().toLowerCase();
    const filtroTipo = document.getElementById("filtro-tipo")?.value || "";
    const filtroStatus = document.getElementById("filtro-status")?.value || "";
    const filtroEmitente = document.getElementById("filtro-emitente")?.value || "";

    const tbody = document.getElementById("tbody-notas");
    const contadorEl = document.getElementById("lbl-contador-notas");
    if (!tbody) return;

    const linhas = [];
    const totalBruto = estado.resultados.length;

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

      if (filtroEmitente) {
        const chaveEmit = (doc?.emitente?.documento || doc?.emitente?.razao_social || "").trim();
        if (chaveEmit !== filtroEmitente) return;
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

      const rotuloEspelho = tipo === "NFSE" ? "📄 DANFSe" : "📄 DANFE";
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
                🔍 Detalhes &amp; Itens
              </button>
              <a class="btn btn-sm btn-ghost" href="/lote/${estado.loteId}/nota/${idx}/xml" download>
                ⬇ XML
              </a>
            </div>
          </td>
        </tr>
      `);
    });

    if (contadorEl) {
      contadorEl.textContent = `Exibindo ${linhas.length} de ${totalBruto} documento(s)`;
    }

    tbody.innerHTML =
      linhas.join("") ||
      '<tr><td colspan="8" style="text-align:center;" class="muted">Nenhum documento corresponde aos filtros aplicados.</td></tr>';
  };

  const abrirModalDetalhes = (idx) => {
    const res = estado.resultados[idx];
    if (!res) return;

    const modal = document.getElementById("modal-detalhes");
    const corpo = document.getElementById("modal-conteudo");
    document.getElementById("modal-titulo").textContent = `Diagnóstico Fiscal & Itens — ${res.arquivo_origem}`;

    const doc = res.documento;
    const cabecalhoHtml = doc
      ? `
        <div class="modal-detail-grid">
          <div class="modal-detail-box">
            <strong>Documento:</strong> ${escaparHtml(doc.tipo)} (Mod. ${escaparHtml(doc.modelo)})<br/>
            <strong>Número / Série:</strong> ${escaparHtml(doc.numero || "-")} / ${escaparHtml(doc.serie || "-")}<br/>
            <strong>Situação:</strong> ${escaparHtml(doc.situacao)}
          </div>
          <div class="modal-detail-box">
            <strong>Emitente:</strong> ${escaparHtml(doc.emitente.razao_social || "-")}<br/>
            <strong>CNPJ/CPF:</strong> <code>${escaparHtml(fmtDoc(doc.emitente.documento))}</code> (${escaparHtml(doc.emitente.uf || "-")})
          </div>
          <div class="modal-detail-box">
            <strong>Valor Produtos/Serviços:</strong> ${fmtMoedaRS(doc.totais.valor_produtos_servicos)}<br/>
            <strong>Valor Total da Nota:</strong> <strong>${fmtMoedaRS(doc.totais.valor_total_nota)}</strong>
          </div>
        </div>
      `
      : "";

    const itens = doc?.itens || [];
    const tabelaItensHtml =
      itens.length > 0
        ? `
          <div style="margin-bottom: 1rem;">
            <h4 style="margin-bottom: 0.45rem;">Itens do Documento (${itens.length})</h4>
            <div class="table-responsive">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Código</th>
                    <th>Descrição</th>
                    <th>NCM / Serv.</th>
                    <th>CFOP</th>
                    <th class="num">Qtd</th>
                    <th class="num">V. Unit</th>
                    <th class="num">V. Total</th>
                  </tr>
                </thead>
                <tbody>
                  ${itens
                    .map(
                      (it) => `
                        <tr>
                          <td>${it.numero_item}</td>
                          <td><code>${escaparHtml(it.codigo || "-")}</code></td>
                          <td>${escaparHtml(it.descricao)}</td>
                          <td><code>${escaparHtml(it.ncm || it.codigo_tributacao_nacional || "-")}</code></td>
                          <td><code>${escaparHtml(it.cfop || "-")}</code></td>
                          <td class="num">${Number(it.quantidade || 0)} ${escaparHtml(it.unidade || "")}</td>
                          <td class="num">${fmtMoeda(it.valor_unitario)}</td>
                          <td class="num"><strong>${fmtMoeda(it.valor_total)}</strong></td>
                        </tr>
                      `
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
          </div>
        `
        : "";

    const alertasHtml =
      (res.alertas || []).length > 0
        ? `<ul style="padding-left: 1.2rem;">${res.alertas
            .map(
              (a) =>
                `<li><strong>[${escaparHtml(a.severidade)}] ${escaparHtml(a.codigo)}:</strong> ${escaparHtml(a.mensagem)}</li>`
            )
            .join("")}</ul>`
        : '<p class="muted">Nenhum erro ou aviso apontado pelos validadores.</p>';

    corpo.innerHTML = `
      ${cabecalhoHtml}
      <div style="margin-bottom: 1rem;">
        <h4 style="margin-bottom: 0.35rem;">Alertas de Validação</h4>
        ${alertasHtml}
      </div>
      ${tabelaItensHtml}
      <details>
        <summary style="cursor:pointer; font-weight:600; margin-bottom:0.45rem;">Ver Estrutura Normalizada Completa (JSON)</summary>
        <pre>${escaparHtml(JSON.stringify(res, null, 2))}</pre>
      </details>
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
      await carregarHistoricoLotes();
      definirStatusUpload(`Lote #${dados.lote_id} salvo no Acervo!`);
      await carregarEscopo("acervo");
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
      await carregarHistoricoLotes();
      definirStatusUpload(`Lote Demo #${dados.lote_id} adicionado ao Acervo!`);
      await carregarEscopo("acervo");
    } catch (err) {
      definirStatusUpload(`Erro ao carregar demonstração: ${err.message}`, true);
    }
  };

  // --- Fase 2 & 3: Recebidas da SEFAZ + Cadastro Visual do Certificado A1 ---
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
    const btnRemoverCert = document.getElementById("btn-remover-cert");
    const badgeAmb = document.getElementById("nsu-badge-ambiente");
    const lblUltNsu = document.getElementById("lbl-ult-nsu");
    const lblMaxNsu = document.getElementById("lbl-max-nsu");
    const lblUltRet = document.getElementById("lbl-ultimo-retorno");
    const bannerBloqueio = document.getElementById("banner-bloqueio-656");
    const btnSync = document.getElementById("btn-sync-sefaz");
    const quickAmb = document.getElementById("quick-select-ambiente");
    const quickUf = document.getElementById("quick-select-uf");
    const modalSelectAmb = document.getElementById("select-cert-ambiente");
    const modalSelectUf = document.getElementById("select-cert-uf");

    if (!badgeCert || !detalhesCert) return;

    try {
      const resp = await fetch("/api/recebidas/status");
      if (!resp.ok) return;
      const st = await resp.json();

      if (quickAmb) quickAmb.value = String(st.ambiente || 2);
      if (quickUf && st.uf_autor) quickUf.value = String(st.uf_autor);
      if (modalSelectAmb) modalSelectAmb.value = String(st.ambiente || 2);
      if (modalSelectUf && st.uf_autor) modalSelectUf.value = String(st.uf_autor);

      if (st.certificado_configurado && st.certificado) {
        const c = st.certificado;
        if (c.vencido) {
          badgeCert.className = "badge badge-err";
          badgeCert.textContent = "Certificado Vencido";
        } else {
          badgeCert.className = "badge badge-ok";
          badgeCert.textContent = `Ativo (${c.dias_para_vencer} dias restantes)`;
        }
        const dtValidade = c.valido_ate ? new Date(c.valido_ate).toLocaleDateString("pt-BR") : "-";
        const origemLbl = st.origem_certificado === "interface" ? "Cadastrado na Interface" : "Variável .env";
        detalhesCert.innerHTML = `
          <div><strong>Titular:</strong> ${escaparHtml(c.titular)}</div>
          <div><strong>CNPJ:</strong> <code>${escaparHtml(fmtDoc(c.documento))}</code> | <strong>Válido até:</strong> ${escaparHtml(dtValidade)}</div>
          <div class="muted"><strong>Emissor:</strong> ${escaparHtml(c.emissor)} • <strong>Origem:</strong> ${escaparHtml(origemLbl)}</div>
        `;
        if (btnRemoverCert) {
          if (st.origem_certificado === "interface") {
            btnRemoverCert.classList.remove("hidden");
          } else {
            btnRemoverCert.classList.add("hidden");
          }
        }
      } else {
        badgeCert.className = "badge badge-warn";
        badgeCert.textContent = "Não Configurado";
        detalhesCert.innerHTML = `
          <div>Nenhum certificado A1 ativo. Clique em <strong>"⚙️ Cadastrar / Trocar Certificado (.pfx)"</strong> para selecionar o arquivo do seu computador.</div>
          <div class="muted" style="margin-top:0.2rem;">Dica: use <strong>"🧪 Carregar Recebidas de Demonstração"</strong> para testar os filtros sem certificado.</div>
        `;
        if (btnRemoverCert) btnRemoverCert.classList.add("hidden");
      }

      if (badgeAmb) {
        badgeAmb.className = st.ambiente === 1 ? "badge badge-ok" : "badge badge-warn";
        badgeAmb.textContent = st.ambiente === 1 ? `Produção (1) • UF ${st.uf_autor || "35"}` : `Homologação (2) • UF ${st.uf_autor || "35"}`;
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

  const abrirModalCertificado = () => {
    const modal = document.getElementById("modal-certificado");
    const feedback = document.getElementById("cert-modal-feedback");
    if (feedback) {
      feedback.className = "cert-feedback hidden";
      feedback.textContent = "";
    }
    if (modal) modal.classList.remove("hidden");
  };

  const fecharModalCertificado = () => {
    const modal = document.getElementById("modal-certificado");
    if (modal) modal.classList.add("hidden");
  };

  const salvarCertificadoModal = async (e) => {
    e.preventDefault();
    const inputArq = document.getElementById("input-cert-arquivo");
    const inputSenha = document.getElementById("input-cert-senha");
    const selectUf = document.getElementById("select-cert-uf");
    const selectAmb = document.getElementById("select-cert-ambiente");
    const feedback = document.getElementById("cert-modal-feedback");
    const btnSalvar = document.getElementById("btn-salvar-certificado");

    if (!inputArq?.files?.length) return;

    const formData = new FormData();
    formData.append("arquivo", inputArq.files[0]);
    formData.append("senha", inputSenha?.value || "");
    formData.append("uf_autor", selectUf?.value || "35");
    formData.append("ambiente", selectAmb?.value || "2");

    if (btnSalvar) btnSalvar.disabled = true;
    if (feedback) {
      feedback.className = "cert-feedback";
      feedback.textContent = "Validando certificado PKCS#12 em memória...";
      feedback.classList.remove("hidden");
    }

    try {
      const resp = await fetch("/api/recebidas/certificado", {
        method: "POST",
        body: formData,
      });
      const dados = await resp.json();
      if (!resp.ok) {
        if (feedback) {
          feedback.className = "cert-feedback err";
          feedback.textContent = dados.detail || "Falha ao validar o certificado digital.";
        }
        return;
      }

      const c = dados.certificado;
      if (feedback && c) {
        feedback.className = "cert-feedback ok";
        feedback.textContent = `✅ Certificado validado e salvo! Titular: ${c.titular} | CNPJ: ${fmtDoc(c.documento)} | ${c.dias_para_vencer} dias restantes.`;
      }
      if (inputSenha) inputSenha.value = "";
      await carregarStatusRecebidas();
      definirStatusRecebidas(`Certificado A1 (${c?.titular || ""}) configurado com sucesso!`);
      setTimeout(fecharModalCertificado, 1100);
    } catch (err) {
      if (feedback) {
        feedback.className = "cert-feedback err";
        feedback.textContent = `Erro de comunicação: ${err.message}`;
      }
    } finally {
      if (btnSalvar) btnSalvar.disabled = false;
    }
  };

  const salvarQuickConfigSefaz = async () => {
    const quickAmb = document.getElementById("quick-select-ambiente");
    const quickUf = document.getElementById("quick-select-uf");
    const ambiente = Number(quickAmb?.value || 2);
    const uf_autor = quickUf?.value || "35";

    try {
      const resp = await fetch("/api/recebidas/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ambiente, uf_autor }),
      });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusRecebidas(dados.detail || "Falha ao atualizar configuração SEFAZ.", true);
        return;
      }
      await carregarStatusRecebidas();
      await carregarListaRecebidas();
      definirStatusRecebidas(
        `Configuração SEFAZ atualizada: Ambiente ${dados.ambiente === 1 ? "Produção (1)" : "Homologação (2)"} • UF ${dados.uf_autor}.`
      );
    } catch (err) {
      definirStatusRecebidas(`Erro ao atualizar configuração: ${err.message}`, true);
    }
  };

  const removerCertificadoSalvo = async () => {
    try {
      const resp = await fetch("/api/recebidas/certificado", { method: "DELETE" });
      if (!resp.ok) return;
      await carregarStatusRecebidas();
      definirStatusRecebidas("Certificado digital removido do computador.");
    } catch (err) {
      definirStatusRecebidas(`Erro ao remover certificado: ${err.message}`, true);
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
                    📄 DANFE
                  </a>
                  <a class="btn btn-sm btn-ghost" href="/recebidas/${chaveUrl}/xml" download>
                    ⬇ XML
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

  document.addEventListener("DOMContentLoaded", async () => {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("file-input");

    const btnTabLotes = document.getElementById("btn-tab-lotes");
    const btnTabRecebidas = document.getElementById("btn-tab-recebidas");
    if (btnTabLotes && btnTabRecebidas) {
      btnTabLotes.addEventListener("click", () => alternarAba("lotes"));
      btnTabRecebidas.addEventListener("click", () => alternarAba("recebidas"));
    }

    // Sub-abas do painel principal
    ["notas", "resumo", "ia"].forEach((sub) => {
      const btn = document.getElementById(`btn-subtab-${sub}`);
      if (btn) btn.addEventListener("click", () => alternarSubaba(sub));
    });

    // Sidebar de Histórico e Escopo
    const btnEscopoAcervo = document.getElementById("btn-escopo-acervo");
    if (btnEscopoAcervo) {
      btnEscopoAcervo.addEventListener("click", () => carregarEscopo("acervo"));
    }

    const listaHistorico = document.getElementById("lista-historico-lotes");
    if (listaHistorico) {
      listaHistorico.addEventListener("click", (e) => {
        const btnDel = e.target.closest("[data-action='excluir-lote']");
        if (btnDel) {
          e.stopPropagation();
          excluirLoteDoHistorico(btnDel.dataset.loteId);
          return;
        }
        const btnSel = e.target.closest("[data-action='selecionar-lote']");
        if (btnSel) {
          carregarEscopo(btnSel.dataset.loteId);
        }
      });
    }

    document.getElementById("btn-limpar-demos")?.addEventListener("click", () => limparHistorico(true));
    document.getElementById("btn-zerar-historico")?.addEventListener("click", () => limparHistorico(false));

    // Clique em um emitente na aba Resumo filtra a aba Notas Fiscais
    document.getElementById("tbody-emitentes")?.addEventListener("click", (e) => {
      const tr = e.target.closest("[data-action='filtrar-emitente']");
      if (!tr) return;
      const chaveEmit = tr.dataset.emitente || "";
      const selectEmit = document.getElementById("filtro-emitente");
      if (selectEmit) {
        selectEmit.value = chaveEmit;
        alternarSubaba("notas");
        renderizarTabelaNotas();
      }
    });

    // Controles SEFAZ e Certificado A1
    document.getElementById("btn-sync-sefaz")?.addEventListener("click", sincronizarComSefaz);
    document.getElementById("btn-demo-recebidas")?.addEventListener("click", carregarRecebidasDemo);
    document.getElementById("btn-abrir-modal-cert")?.addEventListener("click", abrirModalCertificado);
    document.getElementById("btn-card-cadastrar-cert")?.addEventListener("click", abrirModalCertificado);
    document.getElementById("btn-fechar-modal-cert")?.addEventListener("click", fecharModalCertificado);
    document.getElementById("form-cadastro-certificado")?.addEventListener("submit", salvarCertificadoModal);
    document.getElementById("btn-salvar-quick-config")?.addEventListener("click", salvarQuickConfigSefaz);
    document.getElementById("btn-remover-cert")?.addEventListener("click", removerCertificadoSalvo);

    const btnRevelarSenha = document.getElementById("btn-revelar-senha-cert");
    const inputSenhaCert = document.getElementById("input-cert-senha");
    if (btnRevelarSenha && inputSenhaCert) {
      btnRevelarSenha.addEventListener("click", () => {
        const ehPassword = inputSenhaCert.type === "password";
        inputSenhaCert.type = ehPassword ? "text" : "password";
        btnRevelarSenha.textContent = ehPassword ? "🙈 Ocultar" : "👁️ Mostrar";
      });
    }

    const formFiltrosRec = document.getElementById("form-filtros-recebidas");
    if (formFiltrosRec) {
      formFiltrosRec.addEventListener("submit", (e) => {
        e.preventDefault();
        carregarListaRecebidas();
      });
    }

    document.getElementById("btn-rec-limpar")?.addEventListener("click", limparFiltrosRecebidas);

    document.getElementById("btn-select-files")?.addEventListener("click", (e) => {
      e.stopPropagation();
      fileInput.click();
    });

    document.getElementById("btn-empty-upload")?.addEventListener("click", () => fileInput.click());
    document.getElementById("btn-empty-demo")?.addEventListener("click", carregarLoteDemo);

    dropzone?.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      fileInput.click();
    });

    fileInput?.addEventListener("change", () => enviarArquivos(fileInput.files));

    ["dragenter", "dragover"].forEach((evName) => {
      dropzone?.addEventListener(evName, (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((evName) => {
      dropzone?.addEventListener(evName, (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
      });
    });

    dropzone?.addEventListener("drop", (e) => {
      if (e.dataTransfer?.files?.length) {
        enviarArquivos(e.dataTransfer.files);
      }
    });

    document.getElementById("btn-demo")?.addEventListener("click", carregarLoteDemo);
    document.getElementById("btn-demo-inline")?.addEventListener("click", (e) => {
      e.stopPropagation();
      carregarLoteDemo();
    });

    document.getElementById("btn-theme")?.addEventListener("click", () => {
      const html = document.documentElement;
      const atual = html.getAttribute("data-theme") || "dark";
      html.setAttribute("data-theme", atual === "dark" ? "light" : "dark");
    });

    ["filtro-busca", "filtro-tipo", "filtro-status", "filtro-emitente"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("input", renderizarTabelaNotas);
      el.addEventListener("change", renderizarTabelaNotas);
    });

    document.getElementById("tbody-notas")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action='detalhes']");
      if (btn) {
        abrirModalDetalhes(Number(btn.dataset.idx));
      }
    });

    document.getElementById("btn-fechar-modal")?.addEventListener("click", () => {
      document.getElementById("modal-detalhes")?.classList.add("hidden");
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

    // Carregamento automático ao abrir a página: se já existirem lotes no SQLite, abre Todo o Acervo!
    const lotesExistentes = await carregarHistoricoLotes();
    if (lotesExistentes.length > 0) {
      await carregarEscopo("acervo");
    }
  });
})();
