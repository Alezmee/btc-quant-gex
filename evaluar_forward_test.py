"""
Forward-testing del score de confluencia: para cada corrida pasada
guardada en data/historico_snapshots.csv, compara lo que el score decía
en ese momento (régimen esperado + sesgo direccional) contra lo que
realmente pasó con el precio un tiempo después (--horizonte-horas).

No hace falta bajar datos externos: usa las corridas siguientes del
propio histórico como 'precio futuro'. Si todavía no pasó suficiente
tiempo desde una corrida como para tener una corrida posterior que la
resuelva, esa fila queda pendiente (se evalúa sola vez que haya datos
suficientes, corriendo este script de nuevo más adelante).

Uso:
    python3 evaluar_forward_test.py --horizonte-horas 24 --umbral-pct 2.0

Pensado para correr cada tanto (por ejemplo, una vez por día) mientras
automatizar_corridas.py va acumulando snapshots en el fondo.
"""

import argparse
from pathlib import Path

import pandas as pd
import requests

OUT_DIR = Path(__file__).resolve().parent / "data"

DERIBIT_INDEX_URL = "https://www.deribit.com/api/v2/public/get_index_price"


def obtener_precio_actual():
    """Fallback: precio actual de BTC, para evaluar corridas recientes que
    ya cumplieron el horizonte pero todavía no tienen una corrida posterior
    guardada en el histórico."""
    r = requests.get(DERIBIT_INDEX_URL, params={"index_name": "btc_usd"}, timeout=10)
    r.raise_for_status()
    return r.json()["result"]["index_price"]


def clasificar_movimiento(spot_inicial, spot_futuro, umbral_pct):
    cambio_pct = (spot_futuro - spot_inicial) / spot_inicial * 100
    if abs(cambio_pct) < umbral_pct:
        return "rango_sostenido", cambio_pct
    elif cambio_pct > 0:
        return "ruptura_alcista", cambio_pct
    else:
        return "ruptura_bajista", cambio_pct


def resultado_esperado(tipo_regimen, sesgo_score):
    """
    Traduce el score guardado en cada corrida a una predicción concreta,
    para poder compararla contra lo que realmente pasó:
    - Régimen RANGO/REVERSIÓN -> se espera que el precio se mantenga
      dentro de la banda (rango_sostenido)
    - Régimen TENDENCIA/EXPANSIÓN -> se espera ruptura, en la dirección
      que marcaba el sesgo direccional (DEX+Vanna) en ese momento
    """
    tipo_regimen = str(tipo_regimen)
    if "RANGO" in tipo_regimen:
        return "rango_sostenido"
    if pd.isna(sesgo_score):
        return "ruptura_indefinida"
    return "ruptura_alcista" if sesgo_score > 0 else "ruptura_bajista"


def evaluar(historico_path, horizonte_horas, umbral_pct):
    hist = pd.read_csv(historico_path, parse_dates=["timestamp"])
    hist = hist.sort_values("timestamp").reset_index(drop=True)

    if "tipo_regimen" not in hist.columns:
        print("El histórico no tiene columnas de score todavía (son de una versión")
        print("anterior del script). Corré calcular_gex.py con la versión actual")
        print("unas cuantas veces más para empezar a generar filas evaluables.")
        return pd.DataFrame()

    ahora = pd.Timestamp.now(tz="UTC")
    horizonte = pd.Timedelta(hours=horizonte_horas)
    precio_actual_cache = {}

    filas_resultado = []

    for _, fila in hist.iterrows():
        if pd.isna(fila.get("tipo_regimen")):
            continue  # corrida de antes de tener score guardado, no evaluable

        t0 = fila["timestamp"]
        objetivo = t0 + horizonte
        if objetivo > ahora:
            continue  # todavía no pasó suficiente tiempo para esta corrida

        posteriores = hist[hist["timestamp"] >= objetivo]
        if not posteriores.empty:
            spot_futuro = posteriores.iloc[0]["spot"]
            fuente = "historico"
        else:
            if "precio_actual" not in precio_actual_cache:
                try:
                    precio_actual_cache["precio_actual"] = obtener_precio_actual()
                except requests.RequestException as e:
                    print(f"aviso: no se pudo bajar el precio actual ({e}), se omite esta fila")
                    continue
            spot_futuro = precio_actual_cache["precio_actual"]
            fuente = "precio_actual_api"

        resultado, cambio_pct = clasificar_movimiento(fila["spot"], spot_futuro, umbral_pct)
        esperado = resultado_esperado(fila["tipo_regimen"], fila.get("sesgo_score"))
        acierto = resultado == esperado

        filas_resultado.append({
            "timestamp_señal": t0,
            "spot_inicial": fila["spot"],
            "spot_futuro": spot_futuro,
            "cambio_pct": cambio_pct,
            "tipo_regimen": fila["tipo_regimen"],
            "confianza_regimen": fila.get("confianza_regimen"),
            "sesgo_score": fila.get("sesgo_score"),
            "resultado_esperado": esperado,
            "resultado_real": resultado,
            "acierto": acierto,
            "fuente_precio_futuro": fuente,
        })

    return pd.DataFrame(filas_resultado)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizonte-horas", type=float, default=24.0,
                         help="Cuántas horas después de la señal evaluar el resultado")
    parser.add_argument("--umbral-pct", type=float, default=2.0,
                         help="Movimiento mínimo (%) para considerarlo 'ruptura' en vez de 'rango sostenido'")
    args = parser.parse_args()

    historico_path = OUT_DIR / "historico_snapshots.csv"
    if not historico_path.exists():
        print("No existe data/historico_snapshots.csv todavía. Corré calcular_gex.py")
        print("(o automatizar_corridas.py) primero para empezar a generar histórico.")
        return

    resultados = evaluar(historico_path, args.horizonte_horas, args.umbral_pct)

    if resultados.empty:
        print("Todavía no hay corridas con suficiente antigüedad para evaluar")
        print(f"(se necesitan al menos {args.horizonte_horas} horas desde la señal).")
        print("Dejá automatizar_corridas.py corriendo un poco más y volvé a intentar.")
        return

    out_path = OUT_DIR / "forward_test_resultados.csv"
    resultados.to_csv(out_path, index=False)

    total = len(resultados)
    aciertos = resultados["acierto"].sum()
    print(f"Evaluadas {total} señales (horizonte {args.horizonte_horas}h, umbral {args.umbral_pct}%)")
    print(f"Aciertos globales: {aciertos}/{total} ({100*aciertos/total:.1f}%)")

    print("\nDesglose por tipo de régimen:")
    for tipo, grupo in resultados.groupby("tipo_regimen"):
        pct = 100 * grupo["acierto"].sum() / len(grupo)
        print(f"  {tipo}: {grupo['acierto'].sum()}/{len(grupo)} ({pct:.1f}%)")

    print(f"\nResultados detallados guardados en {out_path}")
    print("\nNota: con pocas señales evaluadas todavía esto es apenas indicativo.")
    print("La confiabilidad de estos porcentajes crece con más corridas acumuladas.")


if __name__ == "__main__":
    main()
