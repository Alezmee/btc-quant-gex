# Desplegar el sistema en un VPS (nube)

Guía paso a paso para que `colector_en_vivo.py` y `automatizar_corridas.py`
corran 24/7 en un servidor, en vez de depender de tu PC prendida.

Ejemplo con DigitalOcean, pero los pasos son casi idénticos en cualquier
proveedor (Linode, Vultr, AWS Lightsail, Oracle Cloud Free Tier).

---

## 1. Crear el servidor (VPS)

1. Creá una cuenta en el proveedor que elijas.
2. Creá un "Droplet" (o "instancia") con:
   - **Imagen**: Ubuntu 24.04 LTS
   - **Plan**: el más chico/barato (1 GB de RAM alcanza de sobra)
   - **Región**: la más cercana a vos o a Deribit (Europa/Asia suelen tener
     buena latencia a los exchanges, pero para este uso no es crítico)
3. Vas a recibir una **IP pública** y una contraseña (o configurás una
   clave SSH, más seguro — el proveedor te guía en el paso de creación).

## 2. Conectarte por SSH

Desde tu PC (Windows: usá PowerShell, o instalá PuTTY si preferís GUI):

```bash
ssh root@TU_IP_PUBLICA
```

## 3. Preparar el servidor

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip

# Crear un usuario dedicado (no correr todo como root)
adduser deribit
usermod -aG sudo deribit
su - deribit
```

## 4. Subir los archivos del proyecto

Desde tu PC (no desde el servidor), usando `scp` (o copiá y pegá el
contenido de cada archivo con `nano` directamente en el servidor si
`scp` te complica):

```bash
scp -r CRYPTO/ deribit@TU_IP_PUBLICA:/home/deribit/
```

(reemplazá `CRYPTO/` por la ruta real de tu carpeta local con todos los
`.py` y `requirements.txt`)

## 5. Instalar dependencias en el servidor

Ya conectado por SSH como usuario `deribit`:

```bash
cd /home/deribit/CRYPTO
pip3 install -r requirements.txt --break-system-packages
```

## 6. Probar que anda manualmente antes de automatizar

```bash
python3 calcular_gex.py --max-instrumentos 20
```

Si esto corre bien y te muestra el resumen de GEX, todo lo demás va a
funcionar igual que en tu PC.

## 7. Instalar los servicios de systemd (para que corran solos)

Volvé a tu PC y copiá los archivos `colector-en-vivo.service` y
`automatizar-corridas.service` (los tenés en esta misma carpeta `deploy/`)
al servidor:

```bash
scp deploy/*.service root@TU_IP_PUBLICA:/etc/systemd/system/
```

En el servidor (como root, o con `sudo`):

```bash
systemctl daemon-reload
systemctl enable colector-en-vivo.service
systemctl enable automatizar-corridas.service
systemctl start colector-en-vivo.service
systemctl start automatizar-corridas.service
```

**Verificar que están corriendo:**

```bash
systemctl status colector-en-vivo.service
systemctl status automatizar-corridas.service
```

**Ver los logs en vivo** (útil para debug):

```bash
journalctl -u automatizar-corridas.service -f
```

`-f` = seguir el log en tiempo real, Ctrl+C para salir de la vista (el
servicio sigue corriendo igual).

## 8. Programar el forward-test (una vez por día)

Como usuario `deribit`:

```bash
crontab -e
```

Agregá esta línea al final (corre todos los días a las 9:00 AM UTC):

```
0 9 * * * cd /home/deribit/CRYPTO && /usr/bin/python3 evaluar_forward_test.py --horizonte-horas 24 --umbral-pct 2.0 >> logs/forward_test_cron.log 2>&1
```

## 9. Traer los datos de vuelta a tu PC cuando quieras revisarlos

```bash
scp -r deribit@TU_IP_PUBLICA:/home/deribit/CRYPTO/data ./data_del_servidor
```

---

## Mantenimiento básico

- **Reiniciar un servicio** (por ejemplo si editaste el código):
  ```
  sudo systemctl restart automatizar-corridas.service
  ```
- **Actualizar un script**: subilo de nuevo con `scp` (sobrescribe el
  archivo) y reiniciá el servicio correspondiente.
- **Ver si el servidor sigue vivo**: cualquier proveedor de VPS tiene un
  panel web con métricas básicas (CPU, memoria, disco).
- **Costo**: los planes más chicos ($4-6/mes) alcanzan de sobra para
  esto — es tráfico de red y CPU mínimos.

## Nota de seguridad

Este proyecto solo hace llamadas de **lectura** a APIs públicas de
Deribit (no hay claves de API, no hay ejecución de órdenes, no hay
dinero en juego desde el código en sí). Aun así, es buena práctica:
- No correr nada como `root` en el día a día (por eso el usuario `deribit`).
- Configurar el firewall del proveedor para permitir solo SSH entrante
  (no hace falta abrir ningún puerto más, todo lo demás es tráfico
  saliente hacia Deribit).
