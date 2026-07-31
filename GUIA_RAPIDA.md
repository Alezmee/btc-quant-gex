# Guía rápida — Sistema de análisis GEX/Charm/Vanna/Volga para BTC

## Qué archivos necesitás tener (todos en la misma carpeta)

| Archivo | Para qué sirve |
|---|---|
| `requirements.txt` | Lista de librerías de Python necesarias |
| `backfill_historico.py` | Baja histórico de trades de opciones y de DVOL (una vez, y después de mantenimiento) |
| `colector_en_vivo.py` | Se conecta por WebSocket y va guardando trades/tickers en vivo |
| `calcular_gex.py` | El motor principal: calcula GEX, DEX, Charm, Vanna, Volga, muros, flip point y el score |
| `automatizar_corridas.py` | Corre `calcular_gex.py` solo, cada N minutos, sin que lo hagas a mano |
| `evaluar_forward_test.py` | Compara lo que predijo el score contra lo que pasó de verdad con el precio |
| `deploy/DEPLOY.md` | Guía para correr todo 24/7 en un servidor en la nube (opcional) |
| `deploy/*.service` | Archivos de configuración para la nube (solo hacen falta si desplegás ahí) |
| `api/main.py`, `api/analisis.py` | Backend (FastAPI) que expone el análisis como API |
| `api/requirements.txt` | Librerías extra para el backend (fastapi, uvicorn, matplotlib) |
| `dashboard/index.html` | Dashboard visual — abrilo en el navegador con el backend corriendo |
| `reporte/*` | Generador de reporte ejecutivo en Word (gráficos + narrativa automática) |

Todo vive dentro de una carpeta `data/` que se crea sola la primera vez
que corrés cualquier script (no hace falta crearla vos).

---

## Instalación (una sola vez)

```
pip install -r requirements.txt
```

---

## Orden de uso

### Paso 1 — Backfill histórico (una vez al principio, y de ahí en más como mantenimiento diario/semanal)

```
python backfill_historico.py --dias 30 --max-instrumentos 20
```

La primera vez, pedí bastante historia (`--dias 30`). Las veces
siguientes (mantenimiento), con `--dias 2` alcanza — el script fusiona
con lo que ya tenías, no lo borra.

### Paso 2 — Motor principal de análisis (esto es lo que más vas a usar)

```
python calcular_gex.py --max-instrumentos 100
```

Te tira todo en un momento dado: régimen (GEX), muros, DEX, Vega, Charm,
Vanna, Volga, flip point, y el score de 3 ejes. Cada vez que lo corrés
queda guardado en `data/historico_snapshots.csv`, así se va construyendo
un historial con el que comparar corridas futuras.

**Podés correrlo a mano cuando quieras mirar el mercado**, o dejar que
lo haga solo con el paso 3.

### Paso 3 — Automatizar (para que el histórico se construya sin que hagas nada)

```
python automatizar_corridas.py --intervalo-min 30 --max-instrumentos 100
```

Corre el Paso 2 solo, cada 30 minutos. Dejalo abierto en una ventana de
consola (se puede minimizar). `Ctrl+C` para cortar.

### Paso 4 — Colector en vivo (opcional, en paralelo)

```
python colector_en_vivo.py
```

Guarda trades y tickers en tiempo real vía WebSocket, en
`data/live/`. No es necesario para el score, pero suma datos crudos
por si más adelante querés análisis más finos (order flow, etc.).

### Paso 5 — Forward-testing (después de tener varios días de histórico)

```
python evaluar_forward_test.py --horizonte-horas 24 --umbral-pct 2.0
```

Te dice qué tan seguido acertó el score, comparando cada predicción
pasada contra el precio real 24hs después. Con pocos días de historial
todavía no es confiable — se vuelve útil con el tiempo.

---

### Paso 6 — Backend + Dashboard + Reporte Word (opcional, versión visual)

```
pip install -r api/requirements.txt --break-system-packages
cd api
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Con eso corriendo, abrí `dashboard/index.html` en el navegador (doble
click). Se conecta solo al backend y te muestra todo de forma visual,
con botón para descargar el reporte Word. Ver `README.md` para el
detalle completo de endpoints.

## Rutina diaria recomendada

Si **no** usás la nube (todo desde tu PC):

1. Dejá `automatizar_corridas.py` corriendo mientras la PC esté prendida.
2. Una vez por semana (o cuando la apagues varios días seguidos), corré
   `backfill_historico.py --dias N` (N = días que pasaron desde la
   última vez) para no perder continuidad en trades/DVOL.
3. De vez en cuando, corré `evaluar_forward_test.py` para ver cómo viene
   la performance del score.

Si usás la nube (`deploy/DEPLOY.md`): los pasos 2 y 4 quedan corriendo
solos como servicios 24/7, y programás el paso 5 con `cron` (ya explicado
en la guía de despliegue). Vos solo entrás de vez en cuando a mirar los
resultados.

---

## Qué mirar en `data/` cuando quieras revisar algo a mano

| Archivo | Qué es |
|---|---|
| `historico_snapshots.csv` | Una fila por cada corrida de `calcular_gex.py`, con todas las métricas y el score — el más importante para seguir la evolución |
| `gex_por_strike.csv` | GEX desglosado por strike de la última corrida |
| `muros_calls.csv` / `muros_puts.csv` | Los strikes con mayor concentración de gamma (call wall / put wall) |
| `charm_por_vencimiento.csv`, `vanna_por_vencimiento.csv`, `volga_por_vencimiento.csv` | Desglose de cada griega por fecha de vencimiento |
| `forward_test_resultados.csv` | Resultado de cada predicción evaluada (acierto/error) |
| `trades_opciones_btc.parquet`, `dvol_btc.parquet` | Histórico acumulado de trades y volatilidad |
| `live/` | Trades y tickers en vivo del colector |

Para ver un `.parquet` (no se abre con doble click como un CSV), desde
Python:
```python
import pandas as pd
df = pd.read_parquet("data/trades_opciones_btc.parquet")
print(df.head())
```

---

## Documentación más detallada

- `README.md` — explica cada script en profundidad, con todas las opciones
- `deploy/DEPLOY.md` — cómo desplegar todo en un servidor en la nube
