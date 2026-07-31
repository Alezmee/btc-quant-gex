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

function tablaMetricas() {
  return new Table({
    columnWidths: [4500, 4500],
    width: { size: 9000, type: WidthType.DXA },
    rows: [
      filaTabla("Métrica", "Valor", true),
      filaTabla("Spot BTC", `$${a.spot.toLocaleString("en-US", { maximumFractionDigits: 0 })}`),
      filaTabla("Régimen GEX", a.gex_total > 0 ? "LONG GAMMA (amortiguador)" : "SHORT GAMMA (amplificador)"),
      filaTabla("GEX total", a.gex_total.toLocaleString("en-US", { maximumFractionDigits: 0 })),
      filaTabla("Flip point", `$${a.flip_point.toLocaleString("en-US", { maximumFractionDigits: 0 })}`),
      filaTabla("Call wall", `$${a.muros.call_wall.toLocaleString("en-US", { maximumFractionDigits: 0 })}`),
      filaTabla("Put wall", `$${a.muros.put_wall.toLocaleString("en-US", { maximumFractionDigits: 0 })}`),
      filaTabla("DEX neto (BTC)", a.dex.btc_total.toLocaleString("en-US", { maximumFractionDigits: 1 })),
      filaTabla("Vega neta", a.vega_total.toLocaleString("en-US", { maximumFractionDigits: 1 })),
      filaTabla("CharmEX total (BTC/día)", a.charm.total.toLocaleString("en-US", { maximumFractionDigits: 1 })),
      filaTabla("VannaEX total (BTC/punto IV)", a.vanna.total.toLocaleString("en-US", { maximumFractionDigits: 2 })),
      filaTabla("Ratio Volga/Vega", a.score.ratio_volga_vega.toFixed(2)),
      filaTabla("Sesgo direccional (score)", `${a.score.sesgo_score > 0 ? "+" : ""}${a.score.sesgo_score.toFixed(0)} / 100`),
    ],
  });
}

function parrafoBorde() {
  return new Paragraph({
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "CCCCCC" } },
    spacing: { after: 200 },
  });
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
