"""
Lee los snapshots crudos del dia (clonados desde el repo btc-quant-gex-raw
a ./raw_repo/data/raw/YYYY-MM-DD/*.parquet) y recalcula GEX, DEX, Charm,
Vanna, Volga, muros, flip point, regimen y score de confluencia para cada
snapshot que todavia no este en data/historico_snapshots.csv.

A diferencia de la version anterior, este script NO reimplementa ninguna
formula: importa y reusa las funciones de calcular_gex.py directamente.
Esto es lo que realmente garantiza que sea "la unica fuente de la formula
de calculo" (como decia el docstring original) -- si el dia de manana se
corrige un bug en calcular_gex.py, este script hereda la correccion
automaticamente, sin haber que sincronizar dos copias del mismo codigo.

El formato de la fila que escribe en data/historico_snapshots.csv es
IDENTICO al que produce calcular_gex.py en vivo (mismas columnas:
timestamp, spot, gex_total, flip_point, dex_btc_total, dex_usd_total,
vega_total, charmex_total, vannaex_total, volgaex_magnitud, call_wall,
put_wall, resistencia_cercana, soporte_cercano, tipo_regimen,
confianza_regimen, sesgo_score, sesgo_alineado, riesgo_aceleracion) --
asi el archivo se puede alimentar indistintamente desde corridas en vivo
(automatizar_corridas.py) o desde reprocesamiento de crudo (este script),
sin romper evaluar_forward_test.py ni el calculo de percentiles del score.

Uso:
    python3 calcular_desde_crudo.py --raw-dir ./raw_repo/data/raw
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calcular_gex as gex  # reusa TODAS las formulas y el score desde aca

OUT_DIR = gex.OUT_DIR


def calcular_fila_desde_snapshot(df, timestamp_snapshot):
    """
    Corre exactamente la misma secuencia que calcular_gex.py hace en vivo
    (spot -> gex -> muros -> dex -> vega -> charm -> vanna -> volga ->
    perfil/flip -> score), pero sobre un DataFrame ya descargado en vez
    de pegarle a la API de Deribit. Devuelve la fila lista para el CSV.
    """
    spot, gex_total, _ = gex.calcular_gex_actual(df)
    muros = gex.calcular_muros(df)
    dex_btc_total, dex_usd_total, _ = gex.calcular_dex(df)
    vega_total, _ = gex.calcular_vega_neta(df)
    charmex_total, charm_por_vencimiento = gex.calcular_charm(df)
    vannaex_total, _ = gex.calcular_vanna(df)
    volgaex_total, volgaex_magnitud, _ = gex.calcular_volga(df)

    perfil = gex.calcular_perfil_gex(df)
    flip = gex.encontrar_flip_point(perfil)

    score = gex.calcular_score_confluencia(
        gex_total, dex_btc_total, vega_total, charmex_total,
        vannaex_total, volgaex_magnitud, charm_por_vencimiento,
    )

    return {
        "timestamp": timestamp_snapshot,
        "spot": spot,
        "gex_total": gex_total,
        "flip_point": flip,
        "dex_btc_total": dex_btc_total,
        "dex_usd_total": dex_usd_total,
        "vega_total": vega_total,
        "charmex_total": charmex_total,
        "vannaex_total": vannaex_total,
        "volgaex_magnitud": volgaex_magnitud,
        "call_wall": muros.get("call_wall"),
        "put_wall": muros.get("put_wall"),
        "resistencia_cercana": muros.get("resistencia_cercana"),
        "soporte_cercano": muros.get("soporte_cercano"),
        "tipo_regimen": score.get("tipo_regimen"),
        "confianza_regimen": score.get("confianza_regimen"),
        "sesgo_score": score.get("sesgo_score"),
        "sesgo_alineado": score.get("sesgo_alineado"),
        "riesgo_aceleracion": score.get("riesgo_aceleracion"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=str, default="./raw_repo/data/raw")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    if not raw_dir.exists():
        print(f"No existe {raw_dir}. Revisa que el repo raw este clonado en ese path.")
        return

    hist_path = OUT_DIR / "historico_snapshots.csv"
    hist = gex._leer_historico_seguro(hist_path)
    ya_procesados = set(hist["timestamp"].astype(str)) if "timestamp" in hist.columns and not hist.empty else set()

    archivos = sorted(raw_dir.glob("*/*.parquet"))
    filas_nuevas = 0

    for archivo in archivos:
        df = pd.read_parquet(archivo)
        if df.empty or "snapshot_ts" not in df.columns:
            continue

        ts = str(df["snapshot_ts"].iloc[0])
        if ts in ya_procesados:
            continue  # ya calculado en una corrida anterior

        fila = calcular_fila_desde_snapshot(df, df["snapshot_ts"].iloc[0])
        fila_df = pd.DataFrame([fila])

        hist = pd.concat([hist, fila_df], ignore_index=True, sort=False)
        ya_procesados.add(ts)
        filas_nuevas += 1
        print(f"Procesado {archivo.name}: GEX={fila['gex_total']:,.0f}  regimen={fila['tipo_regimen']}")

    if filas_nuevas == 0:
        print("No hay snapshots crudos nuevos para procesar.")
        return

    hist.to_csv(hist_path, index=False)
    print(f"\n{filas_nuevas} snapshots nuevos agregados a {hist_path}")


if __name__ == "__main__":
    main()
