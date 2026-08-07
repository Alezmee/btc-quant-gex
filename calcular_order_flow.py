"""
Microestructura: Kyle Lambda y VPIN sobre el flujo de trades reales de
BTC-PERPETUAL (no sobre open interest, como el resto del sistema — acá
la fuente de información es el FLUJO de órdenes ejecutándose ahora).

Kyle Lambda: cuántos puntos de precio se mueve el mercado por cada
unidad de volumen neto (comprador - vendedor) ejecutado. Lambda alto =
mercado poco líquido / mucho impacto por orden.

VPIN: proporción del volumen que está desbalanceado entre compras y
ventas, en ventanas de volumen fijo (no de tiempo). Valores altos
sugieren mayor probabilidad de que el flujo esté dominado por
información (traders informados) en vez de ruido aleatorio.

Uso:
    python3 calcular_order_flow.py --minutos-lookback 120
"""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import calcular_gex as gex

OUT_DIR = gex.OUT_DIR
BASE_URL = "https://www.deribit.com/api/v2"


def obtener_trades_recientes(instrument_name, minutos_lookback):
    """
    Trae los trades del instrumento en la ventana pedida, paginando
    hacia atrás igual que hace backfill_historico.py para opciones.
    """
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - minutos_lookback * 60 * 1000

    trades = []
    cursor_end = end_ts
    while True:
        r = requests.get(
            f"{BASE_URL}/public/get_last_trades_by_instrument_and_time",
            params={
                "instrument_name": instrument_name,
                "start_timestamp": start_ts,
                "end_timestamp": cursor_end,
                "count": 1000,
                "include_old": "true",
            },
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json().get("result", {}).get("trades", [])
        if not batch:
            break
        trades.extend(batch)

        oldest_ts = min(t["timestamp"] for t in batch)
        if oldest_ts <= start_ts or len(batch) < 1000:
            break
        cursor_end = oldest_ts - 1
        time.sleep(0.05)

    if not trades:
        return pd.DataFrame(columns=["timestamp", "price", "amount", "direction"])

    df = pd.DataFrame(trades)[["timestamp", "price", "amount", "direction"]]
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df.sort_values("timestamp").reset_index(drop=True)


def calcular_kyle_lambda(df_trades, bucket_segundos=60):
    """
    Bucketiza por tiempo, calcula volumen neto firmado (compras - ventas)
    y cambio de precio por bucket, y ajusta cambio_precio = lambda * volumen_neto
    vía mínimos cuadrados (sin intercepto, como en la formulación clásica de Kyle).
    """
    if df_trades.empty:
        return None

    df = df_trades.copy()
    df["signo"] = np.where(df["direction"] == "buy", 1, -1)
    df["volumen_firmado"] = df["signo"] * df["amount"]
    df["bucket"] = (df["timestamp"] // (bucket_segundos * 1000)).astype(int)

    agg = df.groupby("bucket").agg(
        volumen_neto=("volumen_firmado", "sum"),
        precio_apertura=("price", "first"),
        precio_cierre=("price", "last"),
    ).reset_index()
    agg["cambio_precio"] = agg["precio_cierre"] - agg["precio_apertura"]

    if len(agg) < 5:
        return None

    X = agg["volumen_neto"].values.reshape(-1, 1)
    y = agg["cambio_precio"].values
    coef, residuos, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    lam = coef[0]

    y_pred = X.flatten() * lam
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return {"lambda": lam, "r2": r2, "n_buckets": len(agg)}


def calcular_vpin(df_trades, n_buckets_objetivo=50, ventana_promedio=20):
    """
    VPIN: bucketiza por VOLUMEN fijo (no tiempo), calcula desbalance
    |compras-ventas|/volumen_total por bucket, y promedia sobre una
    ventana móvil de buckets.
    """
    if df_trades.empty:
        return None

    df = df_trades.copy()
    volumen_total = df["amount"].sum()
    if volumen_total <= 0:
        return None

    tamanio_bucket = volumen_total / n_buckets_objetivo
    df["volumen_acumulado"] = df["amount"].cumsum()
    df["bucket"] = (df["volumen_acumulado"] // tamanio_bucket).astype(int)

    df["vol_compra"] = np.where(df["direction"] == "buy", df["amount"], 0.0)
    df["vol_venta"] = np.where(df["direction"] == "sell", df["amount"], 0.0)

    agg = df.groupby("bucket").agg(
        vol_compra=("vol_compra", "sum"),
        vol_venta=("vol_venta", "sum"),
    ).reset_index()
    agg["volumen_bucket"] = agg["vol_compra"] + agg["vol_venta"]
    agg["desbalance"] = (agg["vol_compra"] - agg["vol_venta"]).abs()

    if len(agg) < 3:
        return None

    ventana = min(ventana_promedio, len(agg))
    vpin_serie = agg["desbalance"].rolling(ventana).sum() / agg["volumen_bucket"].rolling(ventana).sum()
    vpin_actual = vpin_serie.iloc[-1]

    return {"vpin": vpin_actual, "n_buckets": len(agg), "tamanio_bucket": tamanio_bucket}


def flujo_opciones_reciente(minutos_lookback=120):
    """
    Complemento: desbalance simple de compras vs ventas en el trade tape
    de OPCIONES que ya venimos acumulando (data/trades_opciones_btc.parquet),
    como proxy rápido de presión direccional del lado de opciones (distinto
    del open interest que usa el resto del sistema).
    """
    path = OUT_DIR / "trades_opciones_btc.parquet"
    if not path.exists():
        return None

    df = pd.read_parquet(path)
    if "direction" not in df.columns or df.empty:
        return None

    corte = int(time.time() * 1000) - minutos_lookback * 60 * 1000
    df = df[df["timestamp"] >= corte]
    if df.empty:
        return None

    vol_compra = df.loc[df["direction"] == "buy", "amount"].sum() if "amount" in df.columns else 0
    vol_venta = df.loc[df["direction"] == "sell", "amount"].sum() if "amount" in df.columns else 0
    total = vol_compra + vol_venta
    if total == 0:
        return None

    return {
        "vol_compra": vol_compra, "vol_venta": vol_venta,
        "desbalance_pct": 100 * (vol_compra - vol_venta) / total,
        "n_trades": len(df),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrumento", default="BTC-PERPETUAL")
    parser.add_argument("--minutos-lookback", type=int, default=120)
    parser.add_argument("--bucket-segundos", type=int, default=60, help="Tamaño de bucket temporal para Kyle Lambda")
    parser.add_argument("--vpin-buckets", type=int, default=50, help="Cantidad de buckets de volumen para VPIN")
    args = parser.parse_args()

    print(f"Bajando trades de {args.instrumento} (últimos {args.minutos_lookback} min)...")
    trades = obtener_trades_recientes(args.instrumento, args.minutos_lookback)
    print(f"  -> {len(trades)} trades obtenidos")

    if trades.empty:
        print("No hubo trades en la ventana pedida (¿mercado muy tranquilo, o ventana muy corta?)")
        return

    print("\n--- Kyle Lambda ---")
    kl = calcular_kyle_lambda(trades, args.bucket_segundos)
    if kl:
        print(f"Lambda: {kl['lambda']:.6f} USD por unidad de volumen neto  (R²={kl['r2']:.3f}, {kl['n_buckets']} buckets)")
        print("  -> cuántos puntos de precio se mueve el mercado por cada BTC neto comprado/vendido")
    else:
        print("No hay suficientes buckets con datos para estimar Kyle Lambda de forma confiable.")

    print("\n--- VPIN ---")
    vp = calcular_vpin(trades, args.vpin_buckets)
    if vp:
        print(f"VPIN actual: {vp['vpin']:.3f}  (0 = balanceado, 1 = totalmente desbalanceado)")
        if vp["vpin"] > 0.6:
            print("  -> desbalance alto: mayor probabilidad de flujo dominado por información")
        elif vp["vpin"] < 0.3:
            print("  -> desbalance bajo: flujo más parecido a ruido balanceado")
        else:
            print("  -> desbalance moderado")
    else:
        print("No hay suficientes buckets de volumen para estimar VPIN de forma confiable.")

    print("\n--- Flujo de opciones (complemento, si hay datos acumulados) ---")
    fo = flujo_opciones_reciente(args.minutos_lookback)
    if fo:
        print(f"Volumen compra: {fo['vol_compra']:.1f}  |  Volumen venta: {fo['vol_venta']:.1f}  ({fo['n_trades']} trades)")
        print(f"Desbalance: {fo['desbalance_pct']:+.1f}% ({'más compras' if fo['desbalance_pct']>0 else 'más ventas'})")
    else:
        print("Sin datos suficientes en data/trades_opciones_btc.parquet para esta ventana")
        print("(corré backfill_historico.py o colector_en_vivo.py primero para acumular trades de opciones)")

    fila = pd.DataFrame([{
        "timestamp": pd.Timestamp.now(tz="UTC"),
        "instrumento": args.instrumento,
        "kyle_lambda": kl["lambda"] if kl else None,
        "kyle_r2": kl["r2"] if kl else None,
        "vpin": vp["vpin"] if vp else None,
        "opciones_desbalance_pct": fo["desbalance_pct"] if fo else None,
    }])
    out_path = OUT_DIR / "order_flow.csv"
    if out_path.exists():
        prev = pd.read_csv(out_path)
        pd.concat([prev, fila], ignore_index=True).to_csv(out_path, index=False)
    else:
        fila.to_csv(out_path, index=False)
    print(f"\nResultado guardado en {out_path}")


if __name__ == "__main__":
    main()
