"""
Colector en vivo de trades y tickers de opciones BTC en Deribit
vía WebSocket público (sin autenticación).

Va guardando los datos en archivos parquet particionados por hora,
listos para sumarlos al set de backtesting.

Uso:
    python3 colector_en_vivo.py
    (Ctrl+C para cortar; guarda lo acumulado antes de salir)
"""

import asyncio
import json
import signal
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import websockets

WS_URL = "wss://www.deribit.com/ws/api/v2"
OUT_DIR = Path("./data/live")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Canales públicos: trades de todas las opciones BTC + ticker agregado
CHANNELS = [
    "trades.option.BTC.100ms",
    "ticker.BTC-PERPETUAL.100ms",  # referencia de precio spot/futuro
]

buffer_trades = []
buffer_tickers = []
seguir_corriendo = True


def guardar_buffers():
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if buffer_trades:
        df = pd.DataFrame(buffer_trades)
        df.to_parquet(OUT_DIR / f"trades_{ts_str}.parquet", index=False)
        print(f"Guardados {len(buffer_trades)} trades -> trades_{ts_str}.parquet")
        buffer_trades.clear()

    if buffer_tickers:
        df = pd.DataFrame(buffer_tickers)
        df.to_parquet(OUT_DIR / f"ticker_{ts_str}.parquet", index=False)
        print(f"Guardados {len(buffer_tickers)} tickers -> ticker_{ts_str}.parquet")
        buffer_tickers.clear()


async def suscribirse(ws):
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "public/subscribe",
        "params": {"channels": CHANNELS},
    }
    await ws.send(json.dumps(msg))


async def escuchar():
    global seguir_corriendo

    async with websockets.connect(WS_URL, ping_interval=15) as ws:
        await suscribirse(ws)
        print("Conectado y suscripto. Acumulando datos... (Ctrl+C para cortar)")

        ultimo_guardado = asyncio.get_event_loop().time()

        while seguir_corriendo:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                pass
            else:
                data = json.loads(raw)
                params = data.get("params", {})
                canal = params.get("channel", "")
                payload = params.get("data")

                if payload is None:
                    continue

                if canal.startswith("trades.option"):
                    # payload es una lista de trades
                    buffer_trades.extend(payload)
                elif canal.startswith("ticker."):
                    buffer_tickers.append(payload)

            # Guardar cada 5 minutos para no perder todo si se cae
            ahora = asyncio.get_event_loop().time()
            if ahora - ultimo_guardado > 300:
                guardar_buffers()
                ultimo_guardado = ahora

        guardar_buffers()


def manejar_corte(signum, frame):
    global seguir_corriendo
    print("\nCortando... guardando lo acumulado.")
    seguir_corriendo = False


if __name__ == "__main__":
    signal.signal(signal.SIGINT, manejar_corte)
    signal.signal(signal.SIGTERM, manejar_corte)
    asyncio.run(escuchar())
