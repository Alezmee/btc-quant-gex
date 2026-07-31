"""
Backend FastAPI para el sistema de análisis de opciones BTC.

Uso:
    uvicorn main:app --host 127.0.0.1 --port 8000 --reload

Docs interactivas (Swagger) en: http://127.0.0.1:8000/docs
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import requests

from analisis import generar_analisis

DERIBIT_INDEX_URL = "https://www.deribit.com/api/v2/public/get_index_price"

app = FastAPI(
    title="BTC Options Analytics API",
    description="GEX, DEX, Charm, Vanna, Volga y score de confluencia para opciones BTC (Deribit)",
    version="1.0.0",
)

# CORS abierto: pensado para uso local/personal (el dashboard corre en tu
# propia máquina). Si en algún momento esto se expone públicamente en un
# servidor, conviene restringir allow_origins a un dominio específico.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/price")
def price():
    """Precio actual del índice BTC/USD de Deribit."""
    try:
        r = requests.get(DERIBIT_INDEX_URL, params={"index_name": "btc_usd"}, timeout=10)
        r.raise_for_status()
        return {"index_price": r.json()["result"]["index_price"]}
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"No se pudo bajar el precio de Deribit: {e}")


@app.get("/api/v1/gex")
def gex_endpoint(
    max_instrumentos: int = Query(100, ge=1, le=1000, description="Cantidad de instrumentos a analizar"),
    rango_pct: float = Query(0.15, gt=0, lt=1, description="Rango +/- para simular el perfil de GEX"),
    guardar_snapshot: bool = Query(True, description="Si se guarda esta corrida en el histórico"),
):
    """Análisis completo: GEX, muros, DEX, Vega, Charm, Vanna, Volga, flip point y score."""
    try:
        return generar_analisis(max_instrumentos, rango_pct, guardar_snapshot)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {e}")


@app.get("/api/v1/download-report")
def download_report(
    max_instrumentos: int = Query(100, ge=1, le=1000),
):
    """Genera y descarga el reporte ejecutivo en Word (.docx)."""
    try:
        # import local para no exigir Node/docx en el arranque del servidor
        # si alguien solo quiere usar los endpoints de datos
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reporte"))
        from generar_reporte import generar_reporte_docx

        path = generar_reporte_docx(max_instrumentos=max_instrumentos)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo generar el reporte: {e}")

    return FileResponse(
        path,
        filename="reporte_btc_gex.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
