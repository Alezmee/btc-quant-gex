"""
Ajusta la parametrización SVI (Stochastic Volatility Inspired, Gatheral)
a la IV de mercado real de cada vencimiento, y deriva el skew a partir
de la curva ajustada (no de dos puntos sueltos).

SVI modela la varianza total w(k) = IV^2 * T en función del log-moneyness
k = ln(K/F), con 5 parámetros (a, b, rho, m, sigma):

    w(k) = a + b * ( rho*(k-m) + sqrt((k-m)^2 + sigma^2) )

Por qué usar esto en vez de la IV cruda por strike: con datos reales hay
ruido strike a strike (poca liquidez en algunos, cotizaciones viejas en
otros). Un ajuste SVI da una curva suave y sin arbitraje entre strikes
(bajo ciertas condiciones sobre los parámetros), de la que se puede leer
un skew y una IV ATM mucho más confiables que mirar puntos sueltos.

Uso:
    python3 calcular_svi.py --max-instrumentos 300
"""

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

import calcular_gex as gex

OUT_DIR = gex.OUT_DIR


def svi_varianza_total(k, a, b, rho, m, sigma):
    """w(k) = a + b*(rho*(k-m) + sqrt((k-m)^2 + sigma^2))"""
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sigma**2))


def ajustar_svi(k_obs, w_obs, intentos=6):
    """
    Ajusta los 5 parámetros SVI minimizando error cuadrático contra la
    varianza total observada. Prueba varios puntos de partida (Nelder-Mead
    puede quedar atrapado en óptimos locales) y se queda con el mejor.
    """
    def costo(params):
        a, b, rho, m, sigma = params
        if b < 0 or abs(rho) >= 1 or sigma <= 0 or a + b * sigma * math.sqrt(1 - rho**2) < 0:
            return 1e6  # fuera de la región que garantiza w(k) >= 0 (sin arbitraje de calendario trivial)
        pred = svi_varianza_total(k_obs, a, b, rho, m, sigma)
        return float(np.sum((pred - w_obs) ** 2))

    w_atm_aprox = float(np.interp(0, k_obs, w_obs))
    mejores = None
    mejor_costo = np.inf

    rng = np.random.default_rng(42)
    for _ in range(intentos):
        x0 = [
            w_atm_aprox * rng.uniform(0.5, 1.0),
            rng.uniform(0.05, 0.3),
            rng.uniform(-0.6, 0.0),  # rho negativo es lo típico (skew normal)
            rng.uniform(-0.1, 0.1),
            rng.uniform(0.05, 0.3),
        ]
        res = minimize(costo, x0, method="Nelder-Mead",
                        options={"xatol": 1e-8, "fatol": 1e-10, "maxiter": 2000})
        if res.fun < mejor_costo:
            mejor_costo = res.fun
            mejores = res.x

    return mejores, mejor_costo


def calcular_skew(params, t_anios):
    """
    Skew simple: diferencia de IV entre un put 10% OTM (k=-0.10) y un
    call 10% OTM (k=+0.10) sobre la curva SVI ajustada. Positivo = puts
    más caras que calls (skew 'normal', típico en equity/índices).
    Negativo o cercano a 0 = skew plano o invertido (a veces se ve en cripto).
    """
    a, b, rho, m, sigma = params
    w_put = svi_varianza_total(-0.10, a, b, rho, m, sigma)
    w_call = svi_varianza_total(0.10, a, b, rho, m, sigma)
    iv_put = math.sqrt(max(w_put, 0) / t_anios) if t_anios > 0 else 0
    iv_call = math.sqrt(max(w_call, 0) / t_anios) if t_anios > 0 else 0
    return iv_put - iv_call, iv_put, iv_call


def procesar_vencimiento(df_venc, spot, t_anios, min_strikes=5):
    """
    Prepara k_obs/w_obs promediando call y put IV por strike (cuando
    ambos existen), y ajusta SVI. Devuelve None si no hay suficientes
    strikes distintos para que el ajuste tenga sentido.
    """
    por_strike = df_venc.groupby("strike")["iv_marcada"].mean().reset_index()
    por_strike = por_strike[por_strike["iv_marcada"] > 0]
    if len(por_strike) < min_strikes:
        return None

    k_obs = np.log(por_strike["strike"].values / spot)
    w_obs = (por_strike["iv_marcada"].values ** 2) * t_anios

    params, error = ajustar_svi(k_obs, w_obs)
    if params is None:
        return None

    rmse = math.sqrt(error / len(k_obs))
    iv_atm = math.sqrt(max(svi_varianza_total(0.0, *params), 0) / t_anios) if t_anios > 0 else 0
    skew, iv_put_10, iv_call_10 = calcular_skew(params, t_anios)

    return {
        "a": params[0], "b": params[1], "rho": params[2], "m": params[3], "sigma": params[4],
        "rmse_iv_aprox": rmse / (2 * max(iv_atm, 1e-6)),  # aprox: error de varianza -> error de IV
        "n_strikes": len(por_strike),
        "iv_atm_pct": 100 * iv_atm,
        "iv_put_10pct_otm_pct": 100 * iv_put_10,
        "iv_call_10pct_otm_pct": 100 * iv_call_10,
        "skew_10pct_pct": 100 * skew,
    }


def calcular_svi_todos_vencimientos(max_instrumentos=300, min_strikes=5):
    df = gex.construir_dataset_gex(max_instrumentos)
    if df.empty:
        raise ValueError("No se obtuvieron datos de Deribit")

    spot = df["spot_subyacente"].dropna().iloc[0]
    resultados = []

    for venc, df_venc in df.groupby("vencimiento"):
        t_anios = df_venc["tiempo_anios"].iloc[0]
        if t_anios <= 0:
            continue
        r = procesar_vencimiento(df_venc, spot, t_anios, min_strikes)
        if r is None:
            continue
        r["vencimiento"] = str(venc)
        r["dias_a_vencimiento"] = round(t_anios * 365, 1)
        resultados.append(r)

    return spot, pd.DataFrame(resultados).sort_values("dias_a_vencimiento") if resultados else pd.DataFrame()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-instrumentos", type=int, default=300)
    parser.add_argument("--min-strikes", type=int, default=5,
                         help="Mínimo de strikes distintos por vencimiento para intentar el ajuste SVI")
    args = parser.parse_args()

    print("Descargando cadena de opciones y ajustando SVI por vencimiento...")
    spot, resultados = calcular_svi_todos_vencimientos(args.max_instrumentos, args.min_strikes)

    if resultados.empty:
        print("No se pudo ajustar SVI a ningún vencimiento (¿pocos strikes por vencimiento?")
        print(f"Probá subir --max-instrumentos o bajar --min-strikes, que hoy es {args.min_strikes}).")
        return

    print(f"\nSpot: {spot:,.0f}")
    print(f"SVI ajustado en {len(resultados)} vencimientos:\n")

    for _, fila in resultados.iterrows():
        print(f"  {fila['vencimiento'][:10]} ({fila['dias_a_vencimiento']:.0f}d, {fila['n_strikes']} strikes):")
        print(f"    IV ATM: {fila['iv_atm_pct']:.1f}%  |  "
              f"put 10% OTM: {fila['iv_put_10pct_otm_pct']:.1f}%  |  "
              f"call 10% OTM: {fila['iv_call_10pct_otm_pct']:.1f}%")
        sesgo = "normal (puts más caras)" if fila["skew_10pct_pct"] > 1 else \
                "invertido (calls más caras)" if fila["skew_10pct_pct"] < -1 else "plano"
        print(f"    Skew (put-call 10% OTM): {fila['skew_10pct_pct']:+.2f} puntos -> {sesgo}")
        print()

    out_path = OUT_DIR / "svi_por_vencimiento.csv"
    resultados.to_csv(out_path, index=False)
    print(f"Resultados guardados en {out_path}")

    # Lectura agregada: term structure de skew
    if len(resultados) >= 2:
        corto = resultados.iloc[0]
        largo = resultados.iloc[-1]
        print(f"\nTerm structure de skew: {corto['skew_10pct_pct']:+.2f} (corto, {corto['dias_a_vencimiento']:.0f}d) "
              f"-> {largo['skew_10pct_pct']:+.2f} (largo, {largo['dias_a_vencimiento']:.0f}d)")
        if corto["skew_10pct_pct"] > largo["skew_10pct_pct"] + 2:
            print("El skew es más pronunciado en el corto plazo -> más demanda de protección inmediata")
        elif largo["skew_10pct_pct"] > corto["skew_10pct_pct"] + 2:
            print("El skew es más pronunciado en el largo plazo -> preocupación estructural, no inmediata")


if __name__ == "__main__":
    main()
