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

    renderizarTabelaNotas();
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

  document.addEventListener("DOMContentLoaded", () => {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("file-input");

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
  });
})();
