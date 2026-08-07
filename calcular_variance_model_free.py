"""
Calcula un índice de volatilidad "model-free" (misma lógica que variance
swaps / metodología VIX) a partir de la cadena de opciones BTC de Deribit,
y lo compara contra el DVOL oficial que publica el propio exchange.

No asume Black-Scholes para el PRICING final (esa es la idea de "model-free":
la fórmula de varianza no depende de ningún modelo) — pero sí usamos
Black-Scholes para reconstruir precios de opciones consistentes a partir
de la IV de mercado que reporta Deribit, en vez de usar precios crudos
del order book (que pueden ser ruidosos o tener poca profundidad en strikes
muy OTM). Esto es una aproximación práctica común, no la fórmula exacta
que usa CBOE para el VIX real (que además filtra por liquidez y usa una
suma discreta con ajustes específicos de strike) — lo aclaramos en los
resultados para no sobre-vender la precisión.

Uso:
    python3 calcular_variance_model_free.py --max-instrumentos 200
"""

import argparse
from pathlib import Path

import pandas as pd
import requests

import calcular_gex as gex

OUT_DIR = gex.OUT_DIR
DERIBIT_VOL_INDEX_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"


def variance_fair_strike_por_vencimiento(df_venc, spot, tiempo_anios, r=0.0):
    """
    Varianza 'fair strike' para un solo vencimiento, vía la integral
    discreta sobre opciones OTM (aproximación de variance swap / VIX):

        Var = (2 * e^(rT) / T) * sum[ (precio_OTM(K) / K^2) * deltaK ]

    Reconstruimos precio_OTM(K) con Black-Scholes usando la IV de mercado
    de cada instrumento (no un IV inventado), agrupando calls por strike
    (para strikes >= spot) y puts (para strikes < spot), como pide la
    metodología estándar.
    """
    df_venc = df_venc.drop_duplicates(subset=["strike", "tipo"]).sort_values("strike")
    strikes = sorted(df_venc["strike"].unique())
    if len(strikes) < 3:
        return None  # muy pocos strikes para que la integral discreta tenga sentido

    precios_otm = {}
    for _, fila in df_venc.iterrows():
        K = fila["strike"]
        iv = fila["iv_marcada"]
        if iv <= 0:
            continue
        if K >= spot and fila["tipo"] == "C":
            precios_otm[K] = gex.bs_call_price(spot, K, tiempo_anios, iv, r)
        elif K < spot and fila["tipo"] == "P":
            precios_otm[K] = gex.bs_put_price(spot, K, tiempo_anios, iv, r)

    strikes_usables = sorted(precios_otm.keys())
    if len(strikes_usables) < 3:
        return None

    total = 0.0
    for i, K in enumerate(strikes_usables):
        if i == 0:
            delta_k = strikes_usables[1] - strikes_usables[0]
        elif i == len(strikes_usables) - 1:
            delta_k = strikes_usables[i] - strikes_usables[i - 1]
        else:
            delta_k = (strikes_usables[i + 1] - strikes_usables[i - 1]) / 2
        total += (precios_otm[K] / K**2) * delta_k

    var_fair = (2 * pow(2.718281828, r * tiempo_anios) / tiempo_anios) * total
    return var_fair


def calcular_variance_model_free(max_instrumentos=200, dias_objetivo=30):
    """
    Réplica simplificada de la metodología VIX: busca los dos vencimientos
    que 'encierran' los dias_objetivo (uno más corto, uno más largo),
    calcula la varianza fair strike de cada uno, e interpola ponderando
    por tiempo para obtener la varianza esperada exactamente a
    dias_objetivo días.
    """
    df = gex.construir_dataset_gex(max_instrumentos)
    if df.empty:
        raise ValueError("No se obtuvieron datos de Deribit")

    spot = df["spot_subyacente"].dropna().iloc[0]
    df["dias_a_vencimiento"] = df["tiempo_anios"] * 365

    vencimientos = sorted(df["vencimiento"].unique())
    resumen_venc = []
    for v in vencimientos:
        dias = df.loc[df["vencimiento"] == v, "dias_a_vencimiento"].iloc[0]
        resumen_venc.append((v, dias))

    anteriores = [rv for rv in resumen_venc if rv[1] <= dias_objetivo]
    posteriores = [rv for rv in resumen_venc if rv[1] > dias_objetivo]

    if not anteriores or not posteriores:
        # No hay vencimientos de sobra a ambos lados: usamos el más cercano solo
        venc_cercano, dias_cercano = min(resumen_venc, key=lambda rv: abs(rv[1] - dias_objetivo))
        df_v = df[df["vencimiento"] == venc_cercano]
        var_fair = variance_fair_strike_por_vencimiento(df_v, spot, dias_cercano / 365)
        if var_fair is None:
            raise ValueError("No hay suficientes strikes para calcular la varianza model-free")
        dvol_calculado = 100 * (var_fair ** 0.5)
        return {
            "spot": spot,
            "dvol_calculado": dvol_calculado,
            "metodo": "vencimiento único (no había vencimientos a ambos lados de 30 días)",
            "vencimiento_usado": str(venc_cercano),
            "dias_usado": dias_cercano,
        }

    venc_corto, dias_corto = max(anteriores, key=lambda rv: rv[1])
    venc_largo, dias_largo = min(posteriores, key=lambda rv: rv[1])

    var_corto = variance_fair_strike_por_vencimiento(df[df["vencimiento"] == venc_corto], spot, dias_corto / 365)
    var_largo = variance_fair_strike_por_vencimiento(df[df["vencimiento"] == venc_largo], spot, dias_largo / 365)

    if var_corto is None or var_largo is None:
        raise ValueError("No hay suficientes strikes en uno de los dos vencimientos para interpolar")

    # Interpolación estilo VIX: ponderar por tiempo en el espacio de "varianza total * T"
    t_corto, t_largo, t_obj = dias_corto / 365, dias_largo / 365, dias_objetivo / 365
    w_corto = var_corto * t_corto
    w_largo = var_largo * t_largo

    peso_corto = (t_largo - t_obj) / (t_largo - t_corto)
    peso_largo = (t_obj - t_corto) / (t_largo - t_corto)

    var_interpolada_total = peso_corto * w_corto + peso_largo * w_largo
    var_30d = var_interpolada_total / t_obj
    dvol_calculado = 100 * (var_30d ** 0.5)

    return {
        "spot": spot,
        "dvol_calculado": dvol_calculado,
        "metodo": "interpolado entre dos vencimientos (estilo VIX)",
        "vencimiento_corto": str(venc_corto), "dias_corto": round(dias_corto, 1), "iv_corto_pct": round(100 * var_corto ** 0.5, 2),
        "vencimiento_largo": str(venc_largo), "dias_largo": round(dias_largo, 1), "iv_largo_pct": round(100 * var_largo ** 0.5, 2),
    }


def obtener_dvol_oficial():
    """Último valor de DVOL publicado por Deribit (para comparar)."""
    import time
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - 2 * 60 * 60 * 1000  # últimas 2 horas alcanzan para tener al menos 1 punto

    r = requests.get(
        DERIBIT_VOL_INDEX_URL,
        params={"currency": "BTC", "start_timestamp": start_ts, "end_timestamp": end_ts, "resolution": "60"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()["result"]["data"]
    if not data:
        return None
    ultimo = data[-1]  # [timestamp, open, high, low, close]
    return ultimo[4]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-instrumentos", type=int, default=200)
    parser.add_argument("--dias-objetivo", type=int, default=30)
    args = parser.parse_args()

    print("Calculando varianza model-free a partir de la cadena de opciones...")
    resultado = calcular_variance_model_free(args.max_instrumentos, args.dias_objetivo)

    print(f"\nSpot: {resultado['spot']:,.0f}")
    print(f"Método: {resultado['metodo']}")
    if "vencimiento_corto" in resultado:
        print(f"  Vencimiento corto: {resultado['vencimiento_corto']} ({resultado['dias_corto']} días, IV~{resultado['iv_corto_pct']}%)")
        print(f"  Vencimiento largo: {resultado['vencimiento_largo']} ({resultado['dias_largo']} días, IV~{resultado['iv_largo_pct']}%)")

    dvol_calc = resultado["dvol_calculado"]
    print(f"\nDVOL calculado (model-free, {args.dias_objetivo} días): {dvol_calc:.2f}")

    try:
        dvol_oficial = obtener_dvol_oficial()
    except requests.RequestException as e:
        dvol_oficial = None
        print(f"(no se pudo bajar el DVOL oficial para comparar: {e})")

    if dvol_oficial is not None:
        diff = dvol_calc - dvol_oficial
        diff_pct = 100 * diff / dvol_oficial
        print(f"DVOL oficial (Deribit, 30 días fijo):              {dvol_oficial:.2f}")
        print(f"Diferencia: {diff:+.2f} puntos ({diff_pct:+.1f}%)")
        if abs(diff_pct) < 5:
            print("-> Coinciden razonablemente bien: buena señal de que los datos están limpios.")
        else:
            print("-> Diferencia notable. Puede deberse a: pocos strikes líquidos en la muestra,")
            print("   vencimientos usados distintos al fijo-30-días exacto de Deribit, o ruido")
            print("   en la reconstrucción de precios vía Black-Scholes con IV de mercado.")

    # Guardar para trackear en el tiempo
    fila = pd.DataFrame([{
        "timestamp": pd.Timestamp.now(tz="UTC"),
        "spot": resultado["spot"],
        "dvol_calculado": dvol_calc,
        "dvol_oficial": dvol_oficial,
    }])
    out_path = OUT_DIR / "variance_model_free.csv"
    if out_path.exists():
        prev = pd.read_csv(out_path)
        pd.concat([prev, fila], ignore_index=True).to_csv(out_path, index=False)
    else:
        fila.to_csv(out_path, index=False)
    print(f"\nResultado guardado en {out_path}")


if __name__ == "__main__":
    main()
