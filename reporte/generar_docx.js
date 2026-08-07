/**
 * Lee datos_reporte.json (generado por preparar_datos_reporte.py) y
 * ensambla el reporte ejecutivo en Word, con gráficos embebidos.
 *
 * Uso: node generar_docx.js <ruta_salida.docx>
 */

const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel,
  Table, TableRow, TableCell, WidthType, ShadingType,
  ImageRun, AlignmentType, BorderStyle,
} = require("docx");

const rutaSalida = process.argv[2] || path.join(__dirname, "reporte_btc_gex.docx");
const datos = JSON.parse(fs.readFileSync(path.join(__dirname, "datos_reporte.json"), "utf-8"));

const a = datos.analisis;

function filaTabla(etiqueta, valor, esHeader = false) {
  const shading = esHeader ? { type: ShadingType.CLEAR, fill: "1F2937" } : undefined;
  const colorTexto = esHeader ? "FFFFFF" : "000000";
  return new TableRow({
    children: [
      new TableCell({
        width: { size: 4500, type: WidthType.DXA },
        shading,
        children: [new Paragraph({ children: [new TextRun({ text: etiqueta, bold: true, color: colorTexto })] })],
      }),
      new TableCell({
        width: { size: 4500, type: WidthType.DXA },
        shading,
        children: [new Paragraph({ children: [new TextRun({ text: String(valor), color: colorTexto })] })],
      }),
    ],
  });
}

function fmtNum(valor, decimales = 0) {
  if (valor === null || valor === undefined) return "N/D";
  return Number(valor).toLocaleString("en-US", { maximumFractionDigits: decimales, minimumFractionDigits: decimales });
}

function fmtUsd(valor, decimales = 0) {
  if (valor === null || valor === undefined) return "no disponible en esta muestra";
  return `$${fmtNum(valor, decimales)}`;
}

function tablaMetricas() {
  return new Table({
    columnWidths: [4500, 4500],
    width: { size: 9000, type: WidthType.DXA },
    rows: [
      filaTabla("Métrica", "Valor", true),
      filaTabla("Spot BTC", fmtUsd(a.spot)),
      filaTabla("Régimen GEX", a.gex_total > 0 ? "LONG GAMMA (amortiguador)" : "SHORT GAMMA (amplificador)"),
      filaTabla("GEX total", fmtNum(a.gex_total)),
      filaTabla("Flip point", fmtUsd(a.flip_point)),
      filaTabla("Call wall", fmtUsd(a.muros.call_wall)),
      filaTabla("Put wall", fmtUsd(a.muros.put_wall)),
      filaTabla("DEX neto (BTC)", fmtNum(a.dex.btc_total, 1)),
      filaTabla("Vega neta", fmtNum(a.vega_total, 1)),
      filaTabla("CharmEX total (BTC/día)", fmtNum(a.charm.total, 1)),
      filaTabla("VannaEX total (BTC/punto IV)", fmtNum(a.vanna.total, 2)),
      filaTabla("Ratio Volga/Vega", a.score.ratio_volga_vega != null ? a.score.ratio_volga_vega.toFixed(2) : "N/D"),
      filaTabla("Sesgo direccional (score)", a.score.sesgo_score != null ? `${a.score.sesgo_score > 0 ? "+" : ""}${a.score.sesgo_score.toFixed(0)} / 100` : "N/D"),
    ],
  });
}

function parrafoBorde() {
  return new Paragraph({
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "CCCCCC" } },
    spacing: { after: 200 },
  });
}

function seccionNoDisponible(motivo) {
  return new Paragraph({
    children: [new TextRun({ text: `No disponible en esta corrida (${motivo}).`, italics: true, color: "888888" })],
    spacing: { after: 150 },
  });
}

function seccionSvi(svi) {
  if (!svi || svi.error || !svi.por_vencimiento || !svi.por_vencimiento.length) {
    return [seccionNoDisponible(svi?.error || "sin vencimientos ajustables")];
  }
  const encabezados = ["Vencimiento", "Días", "Strikes", "IV ATM", "Skew 10% OTM"];
  const filaEncabezado = new TableRow({
    children: encabezados.map((h) => new TableCell({
      shading: { type: ShadingType.CLEAR, fill: "1F2937" },
      children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: "FFFFFF" })] })],
    })),
  });
  const filas = svi.por_vencimiento.map((f) => new TableRow({
    children: [
      f.vencimiento?.slice(0, 10) ?? "-",
      `${f.dias_a_vencimiento?.toFixed(0)}d`,
      `${f.n_strikes}`,
      `${f.iv_atm_pct?.toFixed(1)}%`,
      `${f.skew_10pct_pct >= 0 ? "+" : ""}${f.skew_10pct_pct?.toFixed(2)} pts`,
    ].map((v) => new TableCell({ children: [new Paragraph({ children: [new TextRun({ text: String(v) })] })] })),
  }));
  return [
    new Table({ width: { size: 9000, type: WidthType.DXA }, rows: [filaEncabezado, ...filas] }),
    new Paragraph({
      children: [new TextRun({ text: "Skew positivo = puts más caras que calls (skew \"normal\").", italics: true, color: "555555" })],
      spacing: { before: 100, after: 150 },
    }),
  ];
}

function seccionVolArbitrage(va) {
  if (!va || va.error) return [seccionNoDisponible(va?.error || "sin datos")];
  return [new Paragraph({
    children: [new TextRun({
      text: `IV (DVOL): ${va.iv_dvol_pct?.toFixed(1)}%  |  RV realizada: ${va.rv_realizada_pct?.toFixed(1)}%  |  ` +
            `Spread: ${va.spread >= 0 ? "+" : ""}${va.spread?.toFixed(1)} pts. ${va.lectura ?? ""}`,
    })],
    spacing: { after: 150 },
  })];
}

function seccionVarianceModelFree(vmf) {
  if (!vmf || vmf.error) return [seccionNoDisponible(vmf?.error || "sin datos")];
  const dif = vmf.diferencia != null ? `${vmf.diferencia >= 0 ? "+" : ""}${vmf.diferencia.toFixed(2)} pts (${vmf.diferencia_pct?.toFixed(1)}%)` : "sin comparar";
  return [new Paragraph({
    children: [new TextRun({
      text: `DVOL calculado (model-free): ${vmf.dvol_calculado?.toFixed(2)}  |  DVOL oficial: ${vmf.dvol_oficial?.toFixed(2) ?? "-"}  |  ` +
            `Diferencia: ${dif}. Método: ${vmf.metodo ?? ""}`,
    })],
    spacing: { after: 150 },
  })];
}

function seccionOrderFlow(of_) {
  if (!of_ || of_.error) return [seccionNoDisponible(of_?.error || "sin datos")];
  const flujoOpc = of_.flujo_opciones
    ? `Compra: ${of_.flujo_opciones.vol_compra?.toFixed(1)} · Venta: ${of_.flujo_opciones.vol_venta?.toFixed(1)} · Desbalance: ${of_.flujo_opciones.desbalance_pct >= 0 ? "+" : ""}${of_.flujo_opciones.desbalance_pct?.toFixed(1)}%`
    : "sin datos de opciones acumulados todavía";
  return [new Paragraph({
    children: [new TextRun({
      text: `Kyle Lambda: ${of_.kyle_lambda?.toFixed(5) ?? "-"} (R²=${of_.kyle_r2?.toFixed(2) ?? "-"})  |  VPIN: ${of_.vpin?.toFixed(3) ?? "-"}. ` +
            `Flujo de opciones: ${flujoOpc}`,
    })],
    spacing: { after: 150 },
  })];
}

const doc = new Document({
  sections: [
    {
      properties: {},
      children: [
        new Paragraph({
          text: "Reporte de Opciones BTC",
          heading: HeadingLevel.TITLE,
        }),
        new Paragraph({
          children: [new TextRun({ text: `Generado: ${datos.generado_en}`, italics: true, color: "555555" })],
          spacing: { after: 300 },
        }),

        new Paragraph({ text: "Resumen ejecutivo", heading: HeadingLevel.HEADING_1 }),
        ...datos.narrativa.map(
          (p) => new Paragraph({ children: [new TextRun({ text: p })], spacing: { after: 150 } })
        ),

        parrafoBorde(),

        new Paragraph({ text: "Métricas clave", heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 150 } }),
        tablaMetricas(),

        new Paragraph({ text: "", spacing: { after: 300 } }),

        new Paragraph({ text: "GEX por strike", heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 150 } }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new ImageRun({
              type: "png",
              data: fs.readFileSync(datos.grafico_strike),
              transformation: { width: 600, height: 300 },
            }),
          ],
        }),

        new Paragraph({ text: "", spacing: { after: 200 } }),

        new Paragraph({ text: "Perfil de GEX simulado", heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 150 } }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new ImageRun({
              type: "png",
              data: fs.readFileSync(datos.grafico_perfil),
              transformation: { width: 600, height: 300 },
            }),
          ],
        }),

        new Paragraph({ text: "", spacing: { after: 300 } }),

        new Paragraph({ text: "SVI / Skew por vencimiento", heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 150 } }),
        ...seccionSvi(datos.extra?.svi),

        new Paragraph({ text: "Implied vs. Realized Volatility", heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 150 } }),
        ...seccionVolArbitrage(datos.extra?.vol_arbitrage),

        new Paragraph({ text: "Varianza model-free vs. DVOL", heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 150 } }),
        ...seccionVarianceModelFree(datos.extra?.variance_model_free),

        new Paragraph({ text: "Order Flow: Kyle Lambda / VPIN", heading: HeadingLevel.HEADING_1, spacing: { before: 200, after: 150 } }),
        ...seccionOrderFlow(datos.extra?.order_flow),

        new Paragraph({
          text: "Limitaciones metodológicas",
          heading: HeadingLevel.HEADING_1,
          spacing: { before: 200, after: 150 },
        }),
        new Paragraph({
          children: [new TextRun({
            text: "El signo de GEX/DEX asume que los dealers están del lado contrario al open " +
                  "interest neto de cada strike (convención estándar de la industria, no un dato " +
                  "confirmado). El flip point se estima simulando el spot con la IV de mercado fija; " +
                  "en la realidad el skew de volatilidad cambia de forma dinámica. Charm y Vanna se " +
                  "calculan vía Black-Scholes porque Deribit no los expone directamente. Este reporte " +
                  "es información descriptiva, no una recomendación de inversión.",
          })],
        }),
      ],
    },
  ],
});

Packer.toBuffer(doc).then((buffer) => {
  fs.writeFileSync(rutaSalida, buffer);
  console.log(`Reporte generado: ${rutaSalida}`);
});
