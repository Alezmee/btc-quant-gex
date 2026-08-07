"""
Volatility Arbitrage: compara la volatilidad IMPLÍCITA (DVOL, lo que el
mercado de opciones está pagando) contra la volatilidad REALIZADA
(lo que el precio de BTC efectivamente se movió en los últimos N días).

La lógica (ver Fase 4, sección 15 del roadmap teórico): la IV suele estar
sistemáticamente por encima de la RV la mayor parte del tiempo (la "prima
de riesgo de volatilidad"), lo que históricamente favorece vender opciones
en promedio — con el riesgo de pérdidas grandes en eventos de cola. Esto
es información descriptiva, no una señal de trading lista para ejecutar.

Uso:
    python3 calcular_vol_arbitrage.py --dias-lookback 30 --resolucion 60
"""

import argparse
import math
import time
from pathlib import Path

import pandas as pd
import requests

import calcular_gex as gex
from calcular_variance_model_free import obtener_dvol_oficial

OUT_DIR = gex.OUT_DIR
BASE_URL = "https://www.deribit.com/api/v2"

# Barras por año según la resolución elegida (BTC opera 24/7, sin cierres)
BARRAS_POR_ANIO = {
    "1": 365 * 24 * 60,
    "5": 365 * 24 * 12,
    "15": 365 * 24 * 4,
    "60": 365 * 24,
    "240": 365 * 6,
    "1D": 365,
}


def obtener_velas_precio(instrument_name, resolucion, dias_lookback):
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - dias_lookback * 24 * 60 * 60 * 1000

    r = requests.get(
        f"{BASE_URL}/public/get_tradingview_chart_data",
        params={
            "instrument_name": instrument_name,
            "resolution": resolucion,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()["result"]
    if data.get("status") != "ok" or not data.get("ticks"):
        raise ValueError(f"Deribit no devolvió velas utilizables: {data.get('status')}")

    return pd.DataFrame({
        "timestamp": pd.to_datetime(data["ticks"], unit="ms"),
        "close": data["close"],
    })


def calcular_realized_vol(precios_cierre, barras_por_anio):
    """Volatilidad realizada anualizada, a partir de retornos logarítmicos."""
    precios = pd.Series(precios_cierre).dropna()
    if len(precios) < 10:
        raise ValueError("Muy pocas velas para calcular volatilidad realizada de forma confiable")

    log_returns = precios.apply(math.log).diff().dropna()
    vol_por_barra = log_returns.std(ddof=1)
    return vol_por_barra * math.sqrt(barras_por_anio)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias-lookback", type=int, default=30, help="Ventana de días para la volatilidad realizada")
    parser.add_argument("--resolucion", default="60", choices=list(BARRAS_POR_ANIO.keys()),
                         help="Resolución de las velas en minutos (o '1D' para diario)")
    parser.add_argument("--instrumento", default="BTC-PERPETUAL",
                         help="Instrumento usado como proxy del precio spot (el perpetuo sigue muy de cerca al índice)")
    args = parser.parse_args()

    print(f"Bajando velas de {args.instrumento} ({args.dias_lookback} días, resolución {args.resolucion})...")
    velas = obtener_velas_precio(args.instrumento, args.resolucion, args.dias_lookback)
    print(f"  -> {len(velas)} velas obtenidas")

    rv = calcular_realized_vol(velas["close"], BARRAS_POR_ANIO[args.resolucion])
    rv_pct = 100 * rv
    print(f"\nVolatilidad realizada ({args.dias_lookback}d, anualizada): {rv_pct:.2f}%")

    print("Bajando DVOL oficial (volatilidad implícita actual)...")
    try:
        iv_pct = obtener_dvol_oficial()
    except requests.RequestException as e:
        print(f"No se pudo bajar el DVOL oficial: {e}")
        return

    if iv_pct is None:
        print("Deribit no devolvió un valor de DVOL reciente.")
        return

    print(f"Volatilidad implícita (DVOL actual): {iv_pct:.2f}%")

    spread = iv_pct - rv_pct
    spread_pct_relativo = 100 * spread / rv_pct if rv_pct else None

    print(f"\nSpread IV - RV: {spread:+.2f} puntos", end="")
    if spread_pct_relativo is not None:
        print(f" ({spread_pct_relativo:+.1f}% relativo a la RV)")
    else:
        print()

    UMBRAL = 5.0  # puntos de volatilidad; heurística simple, no un valor "mágico"
    if spread > UMBRAL:
        print(f"-> IV notablemente por encima de RV (> {UMBRAL} puntos): consistente con la prima de")
        print("   riesgo de volatilidad habitual. Contexto históricamente más favorable para vender")
        print("   volatilidad EN PROMEDIO — con riesgo real de pérdidas grandes en eventos de cola.")
    elif spread < -UMBRAL:
        print(f"-> IV notablemente por DEBAJO de RV (< -{UMBRAL} puntos): inusual — el mercado de")
        print("   opciones está pricing menos movimiento del que realmente viene ocurriendo.")
        print("   Vale la pena revisar si hay un evento reciente que movió el precio más de lo esperado.")
    else:
        print(f"-> Spread dentro de un rango normal (+/- {UMBRAL} puntos), sin señal clara en ningún sentido.")

    fila = pd.DataFrame([{
        "timestamp": pd.Timestamp.now(tz="UTC"),
        "iv_dvol": iv_pct,
        "rv_realizada": rv_pct,
        "spread": spread,
        "dias_lookback": args.dias_lookback,
        "resolucion": args.resolucion,
    }])
    out_path = OUT_DIR / "vol_arbitrage.csv"
    if out_path.exists():
        prev = pd.read_csv(out_path)
        pd.concat([prev, fila], ignore_index=True).to_csv(out_path, index=False)
    else:
        fila.to_csv(out_path, index=False)
    print(f"\nResultado guardado en {out_path}")


if __name__ == "__main__":
    main()
