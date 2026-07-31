"""
Lee los snapshots crudos del dia (clonados desde el repo btc-quant-gex-raw
a ./raw_repo/data/raw/YYYY-MM-DD/*.parquet) y recalcula GEX, DEX, Charm,
Vanna, Volga, muros, regimen y score de confluencia para cada snapshot
que todavia no este en data/historico_snapshots.csv.

Este script es la UNICA fuente de la formula de calculo: si el dia de
manana cambias un peso o corregis un bug, corres esto de nuevo apuntando
a todo el historial crudo y el agregado completo se reconstruye desde
cero, consistente de punta a punta.

Uso:
    python3 calcular_desde_crudo.py --raw-dir ./raw_repo/data/raw
"""

import argparse
import math
from pathlib import Path

import pandas as pd

OUT_DIR = Path("./data")
OUT_DIR.mkdir(exist_ok=True)

CONTRACT_SIZE = 1.0


# ---------- Black-Scholes (identico al del repo original) ----------

def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_charm(spot, strike, t, iv, r=0.0):
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return norm_pdf(d1) * d2 / (2 * t)


def bs_vanna(spot, strike, t, iv, r=0.0):
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return -norm_pdf(d1) * d2 / iv


def bs_volga(spot, strike, t, iv, r=0.0):
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    vega = spot * sqrt_t * norm_pdf(d1)
    return vega * d1 * d2 / iv


# ---------- Calculo agregado a partir de un snapshot crudo ----------

def calcular_metricas(df):
    spot = df["spot_subyacente"].dropna().iloc[0]

    # GEX
    df = df.copy()
    df["gex_contrib"] = df["gamma_actual"] * df["open_interest"] * CONTRACT_SIZE * spot**2 * 0.01
    df.loc[df["tipo"] == "P", "gex_contrib"] *= -1
    gex_total = df["gex_contrib"].sum()

    # DEX
    df["dex_btc"] = df["delta_actual"] * df["open_interest"] * CONTRACT_SIZE
    dex_btc_total = df["dex_btc"].sum()
    dex_usd_total = dex_btc_total * spot

    # Vega neta
    df["vega_contrib"] = df["vega_actual"] * df["open_interest"] * CONTRACT_SIZE
    vega_total = df["vega_contrib"].sum()

    # Charm
    df["charm_dia"] = df.apply(
        lambda f: bs_charm(spot, f["strike"], f["tiempo_anios"], f["iv_marcada"]) / 365.0, axis=1
    )
    df["charmex_btc_dia"] = df["charm_dia"] * df["open_interest"] * CONTRACT_SIZE
    df.loc[df["tipo"] == "P", "charmex_btc_dia"] *= -1
    charmex_total = df["charmex_btc_dia"].sum()

    # Vanna
    df["vanna_1pct"] = df.apply(
        lambda f: bs_vanna(spot, f["strike"], f["tiempo_anios"], f["iv_marcada"]) / 100.0, axis=1
    )
    df["vannaex_1pct"] = df["vanna_1pct"] * df["open_interest"] * CONTRACT_SIZE
    df.loc[df["tipo"] == "P", "vannaex_1pct"] *= -1
    vannaex_total = df["vannaex_1pct"].sum()

    # Volga
    df["volga_1pct"] = df.apply(
        lambda f: bs_volga(spot, f["strike"], f["tiempo_anios"], f["iv_marcada"]) / 100.0, axis=1
    )
    df["volgaex_1pct"] = df["volga_1pct"] * df["open_interest"] * CONTRACT_SIZE
    df.loc[df["tipo"] == "P", "volgaex_1pct"] *= -1
    volgaex_magnitud = df["volgaex_1pct"].abs().sum()

    # Muros
    df["gex_abs"] = df["gamma_actual"] * df["open_interest"] * CONTRACT_SIZE * spot**2 * 0.01
    calls = df[df["tipo"] == "C"].groupby("strike")["gex_abs"].sum().sort_values(ascending=False)
    puts = df[df["tipo"] == "P"].groupby("strike")["gex_abs"].sum().sort_values(ascending=False)
    call_wall = calls.index[0] if len(calls) else None
    put_wall = puts.index[0] if len(puts) else None

    return {
        "spot": spot,
        "gex_total": gex_total,
        "dex_btc_total": dex_btc_total,
        "dex_usd_total": dex_usd_total,
        "vega_total": vega_total,
        "charmex_total": charmex_total,
        "vannaex_total": vannaex_total,
        "volgaex_magnitud": volgaex_magnitud,
        "call_wall": call_wall,
        "put_wall": put_wall,
    }


def _percentil(valor, serie):
    serie = serie.dropna()
    if len(serie) == 0:
        return 50.0
    return float((serie <= valor).mean() * 100)


def calcular_score(metricas, hist):
    gex_total = metricas["gex_total"]
    dex_btc_total = metricas["dex_btc_total"]
    vannaex_total = metricas["vannaex_total"]

    pct_gex = _percentil(abs(gex_total), hist["gex_total"].abs()) if "gex_total" in hist and not hist.empty else 50.0
    confianza_regimen = max(0, min(100, 50 + 0.5 * (pct_gex - 50)))
    tipo_regimen = "RANGO / REVERSION (long gamma)" if gex_total > 0 else "TENDENCIA / EXPANSION (short gamma)"

    dex_signo = 1 if dex_btc_total > 0 else -1
    vanna_signo = 1 if vannaex_total > 0 else -1
    alineados = dex_signo == vanna_signo
    pct_dex = _percentil(abs(dex_btc_total), hist["dex_btc_total"].abs()) if "dex_btc_total" in hist and not hist.empty else 50.0
    fuerza = pct_dex / 100.0
    sesgo_score = dex_signo * (50 + 50 * fuerza * 0.5) if alineados else dex_signo * (15 * fuerza)
    sesgo_score = max(-100, min(100, sesgo_score))

    return {
        "tipo_regimen": tipo_regimen,
        "confianza_regimen": confianza_regimen,
        "sesgo_score": sesgo_score,
        "sesgo_alineado": alineados,
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
    hist = pd.read_csv(hist_path) if hist_path.exists() else pd.DataFrame()
    ya_procesados = set(hist["snapshot_ts"]) if "snapshot_ts" in hist.columns else set()

    archivos = sorted(raw_dir.glob("*/*.parquet"))
    filas_nuevas = []

    for archivo in archivos:
        df = pd.read_parquet(archivo)
        if df.empty:
            continue
        ts = str(df["snapshot_ts"].iloc[0])
        if ts in ya_procesados:
            continue  # ya calculado en una corrida anterior

        metricas = calcular_metricas(df)
        score = calcular_score(metricas, hist if not hist.empty else pd.concat([hist] + [pd.DataFrame(filas_nuevas)], ignore_index=True))

        filas_nuevas.append({
            "snapshot_ts": ts,
            **metricas,
            **score,
        })
        print(f"Procesado {archivo.name}: GEX={metricas['gex_total']:,.0f} regimen={score['tipo_regimen']}")

    if not filas_nuevas:
        print("No hay snapshots crudos nuevos para procesar.")
        return

    nuevo = pd.DataFrame(filas_nuevas)
    combinado = pd.concat([hist, nuevo], ignore_index=True, sort=False)
    combinado.to_csv(hist_path, index=False)
    print(f"\n{len(filas_nuevas)} snapshots nuevos agregados a {hist_path}")


if __name__ == "__main__":
    main()
