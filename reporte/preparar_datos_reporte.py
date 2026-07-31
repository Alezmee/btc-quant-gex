"""
Corre el análisis (reutilizando api/analisis.py) y prepara todo lo que
necesita el reporte Word: un JSON con las métricas + narrativa, y los
gráficos como PNG (matplotlib), para que generar_docx.js los ensamble.
"""

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # sin display, solo generar archivos
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from analisis import generar_analisis  # noqa: E402

GRAFICOS_DIR = Path(__file__).resolve().parent / "graficos"
GRAFICOS_DIR.mkdir(exist_ok=True)

COLOR_CALL = "#2E7D32"
COLOR_PUT = "#C62828"
COLOR_SPOT = "#1565C0"
COLOR_FLIP = "#EF6C00"


def _grafico_gex_por_strike(por_strike, spot, flip_point, out_path):
    strikes = [f["strike"] for f in por_strike]
    valores = [f["gex_contrib"] for f in por_strike]
    colores = [COLOR_CALL if v >= 0 else COLOR_PUT for v in valores]

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(strikes, valores, color=colores, width=(max(strikes) - min(strikes)) / max(len(strikes), 1) * 0.8)
    ax.axvline(spot, color=COLOR_SPOT, linestyle="--", linewidth=1.5, label=f"Spot ({spot:,.0f})")
    if flip_point:
        ax.axvline(flip_point, color=COLOR_FLIP, linestyle=":", linewidth=1.5, label=f"Flip point ({flip_point:,.0f})")
    ax.set_title("GEX por strike")
    ax.set_xlabel("Strike (USD)")
    ax.set_ylabel("GEX")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _grafico_perfil_gex(perfil, spot, flip_point, out_path):
    spots_hip = [f["spot_hipotetico"] for f in perfil]
    gex_hip = [f["gex_total"] for f in perfil]

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(spots_hip, gex_hip, color=COLOR_SPOT, linewidth=2)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.axvline(spot, color=COLOR_SPOT, linestyle="--", linewidth=1.2, label=f"Spot actual ({spot:,.0f})")
    if flip_point:
        ax.axvline(flip_point, color=COLOR_FLIP, linestyle=":", linewidth=1.5, label=f"Flip point ({flip_point:,.0f})")
    ax.fill_between(spots_hip, gex_hip, 0, where=[g >= 0 for g in gex_hip], color=COLOR_CALL, alpha=0.15)
    ax.fill_between(spots_hip, gex_hip, 0, where=[g < 0 for g in gex_hip], color=COLOR_PUT, alpha=0.15)
    ax.set_title("Perfil de GEX simulado por nivel de spot")
    ax.set_xlabel("Spot hipotético (USD)")
    ax.set_ylabel("GEX total")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _narrativa(analisis):
    regimen_txt = "positivo (LONG GAMMA, amortiguador)" if analisis["gex_total"] > 0 else "negativo (SHORT GAMMA, amplificador)"
    sesgo = analisis["score"]["sesgo_score"]
    sesgo_txt = "alcista" if sesgo > 0 else "bajista" if sesgo < 0 else "neutral"
    alineado_txt = "confirmado por DEX y Vanna" if analisis["score"]["sesgo_alineado"] else "sin confirmación de Vanna (más débil)"

    parrafos = [
        f"Con el spot de BTC en {analisis['spot']:,.0f} USD, el régimen de gamma actual es "
        f"{regimen_txt}. El flip point estimado está en {analisis['flip_point']:,.0f} USD.",

        f"El call wall (mayor concentración de gamma del lado calls) se ubica en "
        f"{analisis['muros']['call_wall']:,.0f}, mientras que el put wall está en "
        f"{analisis['muros']['put_wall']:,.0f}. La resistencia más cercana al spot es "
        f"{analisis['muros']['resistencia_cercana']:,.0f} y el soporte más cercano es "
        f"{analisis['muros']['soporte_cercano']:,.0f}.",

        f"El sesgo direccional del score es {sesgo_txt} ({sesgo:+.0f} en una escala de -100 a +100), "
        f"{alineado_txt}.",

        f"El ratio Volga/Vega es de {analisis['score']['ratio_volga_vega']:.2f}, lo que indica "
        f"{'una convexidad alta' if analisis['score']['ratio_volga_vega'] > 1 else 'una convexidad moderada/baja'} "
        f"en la exposición a volatilidad — relevante si ocurre un shock de IV grande.",

        "Nota metodológica: estas métricas asumen que los dealers están del lado contrario al "
        "open interest neto de cada strike (convención estándar de la industria, no un dato "
        "confirmado). Esto es información descriptiva para apoyar decisiones, no una señal de "
        "entrada o salida en sí misma.",
    ]
    return parrafos


def preparar_reporte(max_instrumentos=100, rango_pct=0.15):
    analisis = generar_analisis(max_instrumentos=max_instrumentos, rango_pct=rango_pct, guardar_snapshot=True)

    grafico_strike_path = GRAFICOS_DIR / "gex_por_strike.png"
    grafico_perfil_path = GRAFICOS_DIR / "perfil_gex.png"

    _grafico_gex_por_strike(analisis["por_strike"], analisis["spot"], analisis["flip_point"], grafico_strike_path)
    _grafico_perfil_gex(analisis["perfil_gex"], analisis["spot"], analisis["flip_point"], grafico_perfil_path)

    datos_reporte = {
        "analisis": analisis,
        "narrativa": _narrativa(analisis),
        "grafico_strike": str(grafico_strike_path),
        "grafico_perfil": str(grafico_perfil_path),
        "generado_en": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
    }

    out_json = Path(__file__).resolve().parent / "datos_reporte.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(datos_reporte, f, ensure_ascii=False, indent=2)

    return out_json


if __name__ == "__main__":
    ruta = preparar_reporte()
    print(f"Datos y gráficos del reporte listos: {ruta}")
