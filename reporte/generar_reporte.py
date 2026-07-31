"""
Orquesta la generación completa del reporte Word:
1. Corre el análisis y genera los gráficos (preparar_datos_reporte.py)
2. Llama a Node (generar_docx.js) para ensamblar el .docx final

Se puede usar como script de consola:
    python3 generar_reporte.py --max-instrumentos 100

O importarse desde el backend (api/main.py) vía generar_reporte_docx().
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preparar_datos_reporte as prep  # noqa: E402

REPORTE_DIR = Path(__file__).resolve().parent


def generar_reporte_docx(max_instrumentos: int = 100, rango_pct: float = 0.15) -> str:
    """Devuelve la ruta al .docx generado."""
    prep.preparar_reporte(max_instrumentos=max_instrumentos, rango_pct=rango_pct)

    salida = REPORTE_DIR / "reporte_btc_gex.docx"
    resultado = subprocess.run(
        ["node", str(REPORTE_DIR / "generar_docx.js"), str(salida)],
        capture_output=True, text=True, cwd=str(REPORTE_DIR),
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"Error generando el .docx: {resultado.stderr}")

    return str(salida)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-instrumentos", type=int, default=100)
    parser.add_argument("--rango-pct", type=float, default=0.15)
    args = parser.parse_args()

    print("Generando análisis y gráficos...")
    ruta = generar_reporte_docx(args.max_instrumentos, args.rango_pct)
    print(f"Listo: {ruta}")


if __name__ == "__main__":
    main()
