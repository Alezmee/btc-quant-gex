"""
Corre calcular_gex.py en loop, cada N minutos, para ir construyendo el
histórico (data/historico_snapshots.csv) sin tener que ejecutarlo a mano
cada vez.

Guarda además el output completo de cada corrida en logs/, con
timestamp, por si querés revisar qué pasó en una corrida puntual.

Uso:
    python3 automatizar_corridas.py --intervalo-min 30 --max-instrumentos 100

Cortar con Ctrl+C (termina la corrida actual si hay una en curso, no la
interrumpe a la mitad).
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

seguir_corriendo = True


def correr_una_vez(max_instrumentos, rango_pct):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"corrida_{ts}.log"

    comando = [
        sys.executable,  # usa el mismo intérprete de Python que está corriendo este script
        "calcular_gex.py",
        "--max-instrumentos", str(max_instrumentos),
        "--rango-pct", str(rango_pct),
    ]

    print(f"[{ts}] Corriendo calcular_gex.py (max-instrumentos={max_instrumentos})...")

    try:
        resultado = subprocess.run(
            comando, capture_output=True, text=True, timeout=1800  # 30 min de margen
        )
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(resultado.stdout)
            if resultado.stderr:
                f.write("\n--- STDERR ---\n")
                f.write(resultado.stderr)

        if resultado.returncode == 0:
            print(f"  -> OK. Log guardado en {log_path}")
        else:
            print(f"  -> La corrida terminó con error (código {resultado.returncode}).")
            print(f"     Revisá el detalle en {log_path}")

    except subprocess.TimeoutExpired:
        print(f"  -> La corrida tardó más de 30 minutos y se cortó. Revisá tu conexión o bajá --max-instrumentos.")
    except Exception as e:
        print(f"  -> Error inesperado al correr calcular_gex.py: {e}")


def main():
    global seguir_corriendo

    parser = argparse.ArgumentParser()
    parser.add_argument("--intervalo-min", type=int, default=30, help="Minutos entre corridas")
    parser.add_argument("--max-instrumentos", type=int, default=100)
    parser.add_argument("--rango-pct", type=float, default=0.15)
    args = parser.parse_args()

    print(f"Automatizando corridas de calcular_gex.py cada {args.intervalo_min} minutos.")
    print("Ctrl+C para cortar (deja terminar la corrida en curso antes de salir).\n")

    while seguir_corriendo:
        try:
            correr_una_vez(args.max_instrumentos, args.rango_pct)
        except KeyboardInterrupt:
            print("\nCortando automatización...")
            break

        print(f"Esperando {args.intervalo_min} minutos hasta la próxima corrida...\n")
        try:
            time.sleep(args.intervalo_min * 60)
        except KeyboardInterrupt:
            print("\nCortando automatización...")
            break


if __name__ == "__main__":
    main()
