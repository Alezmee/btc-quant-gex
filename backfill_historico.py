"""
Backfill histórico de opciones BTC en Deribit (API pública, sin auth).
Descarga trades históricos por instrumento y los guarda en parquet,
listos para backtesting.

Uso:
    python3 backfill_historico.py --dias 7
"""

import argparse
import time
import requests
import pandas as pd
from pathlib import Path

BASE_URL = "https://www.deribit.com/api/v2"
OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR.mkdir(exist_ok=True)


def get_instrumentos_btc_options():
    """Lista todos los instrumentos de opciones BTC activos."""
    r = requests.get(
        f"{BASE_URL}/public/get_instruments",
        params={"currency": "BTC", "kind": "option", "expired": "false"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["result"]


def get_trades_por_instrumento(instrument_name, start_ts_ms, end_ts_ms):
    """
    Trae todos los trades históricos de un instrumento en un rango de tiempo.
    Pagina automáticamente usando el timestamp del último trade recibido.
    """
    trades = []
    cursor_end = end_ts_ms

    while True:
        r = requests.get(
            f"{BASE_URL}/public/get_last_trades_by_instrument_and_time",
            params={
                "instrument_name": instrument_name,
                "start_timestamp": start_ts_ms,
                "end_timestamp": cursor_end,
                "count": 1000,
                "include_old": "true",
            },
            timeout=15,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        batch = result.get("trades", [])
        if not batch:
            break

        trades.extend(batch)

        oldest_ts = min(t["timestamp"] for t in batch)
        if oldest_ts <= start_ts_ms or len(batch) < 1000:
            break
        cursor_end = oldest_ts - 1  # seguimos paginando hacia atrás

        time.sleep(0.1)  # no golpear el rate limit

    return trades


def get_volatilidad_index(start_ts_ms, end_ts_ms, resolution="60"):
    """DVOL histórico (índice de volatilidad implícita) para BTC."""
    r = requests.get(
        f"{BASE_URL}/public/get_volatility_index_data",
        params={
            "currency": "BTC",
            "start_timestamp": start_ts_ms,
            "end_timestamp": end_ts_ms,
            "resolution": resolution,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()["result"]["data"]
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=7, help="Días hacia atrás a descargar")
    parser.add_argument(
        "--max-instrumentos",
        type=int,
        default=20,
        help="Límite de instrumentos a bajar (para no saturar en pruebas)",
    )
    args = parser.parse_args()

    end_ts = int(time.time() * 1000)
    start_ts = end_ts - args.dias * 24 * 60 * 60 * 1000

    print("Descargando DVOL (índice de volatilidad BTC)...")
    dvol_nuevo = get_volatilidad_index(start_ts, end_ts)
    dvol_path = OUT_DIR / "dvol_btc.parquet"
    if dvol_path.exists():
        dvol_previo = pd.read_parquet(dvol_path)
        dvol = pd.concat([dvol_previo, dvol_nuevo], ignore_index=True)
        dvol = dvol.drop_duplicates(subset="timestamp").sort_values("timestamp")
    else:
        dvol = dvol_nuevo
    dvol.to_parquet(dvol_path, index=False)
    print(f"  -> {len(dvol)} filas totales acumuladas en data/dvol_btc.parquet ({len(dvol_nuevo)} nuevas esta corrida)")

    print("Listando instrumentos de opciones BTC activos...")
    instrumentos = get_instrumentos_btc_options()
    print(f"  -> {len(instrumentos)} instrumentos encontrados")

    nombres = [i["instrument_name"] for i in instrumentos][: args.max_instrumentos]

    todos_trades = []
    for idx, nombre in enumerate(nombres, 1):
        print(f"  [{idx}/{len(nombres)}] Trades de {nombre}...")
        trades = get_trades_por_instrumento(nombre, start_ts, end_ts)
        todos_trades.extend(trades)
        time.sleep(0.1)

    out_path = OUT_DIR / "trades_opciones_btc.parquet"
    if todos_trades:
        df_nuevo = pd.DataFrame(todos_trades)
        df_nuevo["datetime"] = pd.to_datetime(df_nuevo["timestamp"], unit="ms")

        if out_path.exists():
            df_previo = pd.read_parquet(out_path)
            df = pd.concat([df_previo, df_nuevo], ignore_index=True)
            # 'trade_id' es el identificador único de Deribit para cada trade
            columna_id = "trade_id" if "trade_id" in df.columns else None
            if columna_id:
                df = df.drop_duplicates(subset=columna_id)
            else:
                df = df.drop_duplicates()
            df = df.sort_values("timestamp")
        else:
            df = df_nuevo

        df.to_parquet(out_path, index=False)
        print(f"\nListo: {len(df)} trades totales acumulados en {out_path} ({len(df_nuevo)} nuevos esta corrida)")
    else:
        print("\nNo se encontraron trades nuevos en el rango pedido.")
        if out_path.exists():
            df_previo = pd.read_parquet(out_path)
            print(f"(el archivo acumulado sigue teniendo {len(df_previo)} trades de corridas anteriores)")


if __name__ == "__main__":
    main()
