"""
Servidor local para el panel de sensores Cudeca (Ubuntu).

Sirve:
  - El proyecto (HTML/CSS/media) bajo /analizadores/...
  - Listado JSON de los CSV del Synology en /analizadores/api/files
  - Los CSV en /analizadores/data/<nombre>

----------------------------------------------------------------------
PREPARACION EN UBUNTU
----------------------------------------------------------------------

1) Instala el cliente CIFS y monta el recurso del Synology:

     sudo apt update
     sudo apt install cifs-utils
     sudo mkdir -p /mnt/uidee_analizadores

   IMPORTANTE: el share real es "homes" (no "UIDEE_admin"). La ruta SMB
   que funciona desde Nautilus es:
       smb://172.16.0.117/homes/UIDEE_admin/3.DESAROLLO/HA_DATOS_ANALIZADORES

   Crea un fichero de credenciales (NO las metas en /etc/fstab en claro):

     sudo nano /etc/cifs-uidee.cred
       username=UIDEE_admin
       password=*** (tu password) ***
       domain=WORKGROUP
     sudo chmod 600 /etc/cifs-uidee.cred

   Monta el recurso (share = homes, subcarpeta via "dir_mode"/prefixpath):

     sudo mount -t cifs \\
       "//172.16.0.117/homes" \\
       /mnt/uidee_analizadores \\
       -o credentials=/etc/cifs-uidee.cred,uid=$(id -u),gid=$(id -g),iocharset=utf8,vers=3.0,file_mode=0644,dir_mode=0755

   Luego apunta DATA_DIR a la subcarpeta dentro del montaje:

     export UIDEE_DATA_DIR="/mnt/uidee_analizadores/UIDEE_admin/3.DESAROLLO/HA_DATOS_ANALIZADORES"

   Para que se monte siempre al arrancar, anade en /etc/fstab:

     //172.16.0.117/homes  /mnt/uidee_analizadores  cifs  credentials=/etc/cifs-uidee.cred,uid=1000,gid=1000,iocharset=utf8,vers=3.0,file_mode=0644,dir_mode=0755,_netdev  0  0

   ALTERNATIVA sin sudo (gvfs, ya lo usa Nautilus):
     gio mount "smb://UIDEE_admin@172.16.0.117/homes/UIDEE_admin/3.DESAROLLO/HA_DATOS_ANALIZADORES"
     export UIDEE_DATA_DIR="/run/user/$(id -u)/gvfs/smb-share:server=172.16.0.117,share=homes/UIDEE_admin/3.DESAROLLO/HA_DATOS_ANALIZADORES"

2) Coloca este script y la carpeta del proyecto donde quieras, p.ej.:

     /home/usuario/analizadores/
        cudeca.html        <- el HTML
        cudeca.css
        media/LogoBlancoV3.png
        server.py          <- este fichero

3) Arranca el servidor:

     python3 server.py

4) Abre en el navegador:

     http://localhost:8000/analizadores/cudeca.html

   (El HTML es cudeca.html. Si lo renombras a analizadores.html cambia
   la URL en consecuencia; el servidor sirve cualquier archivo de la
   carpeta del proyecto.)

NOTA DE SEGURIDAD: por defecto este servidor escucha solo en 127.0.0.1
para que NO quede expuesto en la red de la empresa. Si necesitas que
otros equipos accedan, cambia HOST a "0.0.0.0" y pon el servidor detras
de un proxy con autenticacion. NUNCA dejes la contrasena del Synology
en codigo cliente; se gestiona en el montaje CIFS.
"""

import http.server
import socketserver
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

# ----------------------------------------------------------------------
# CONFIGURACION
# ----------------------------------------------------------------------

# Carpeta donde estan los CSV (recurso CIFS del Synology montado).
# Se puede sobreescribir con la variable de entorno UIDEE_DATA_DIR, p.ej.:
#   export UIDEE_DATA_DIR="/run/user/1000/gvfs/smb-share:server=172.16.0.117,share=homes/UIDEE_admin/3.DESAROLLO/HA_DATOS_ANALIZADORES"
DATA_DIR = Path(
    os.environ.get(
        "UIDEE_DATA_DIR",
        "/mnt/uidee_analizadores/UIDEE_admin/3.DESAROLLO/HA_DATOS_ANALIZADORES",
    )
)

# Carpeta del proyecto (donde esta cudeca.html). Por defecto, junto a este script.
PROJECT_DIR = Path(__file__).parent.resolve()

# Prefijo bajo el que se sirve el proyecto. Con esto, el HTML queda en
#   http://HOST:PORT/analizadores/cudeca.html
# y los fetch relativos del JS resuelven a /analizadores/api/files y
# /analizadores/data/<archivo>, que es lo que enruta este servidor.
URL_PREFIX = "/analizadores"

PORT = 8000
HOST = "127.0.0.1"   # cambia a "0.0.0.0" si lo quieres accesible en LAN

# ----------------------------------------------------------------------


class Handler(http.server.SimpleHTTPRequestHandler):

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        try:
            data = path.read_bytes()
        except Exception as e:
            self.send_error(500, str(e))
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        raw_path = urlsplit(self.path).path

        # Quita el prefijo /analizadores si esta presente
        if raw_path.startswith(URL_PREFIX):
            sub = raw_path[len(URL_PREFIX):] or "/"
        else:
            sub = raw_path

        # --- API: listado de CSV ---
        if sub.rstrip("/") == "/api/files":
            try:
                if not DATA_DIR.exists():
                    self._send_json(
                        {"error": f"No se encuentra {DATA_DIR}. "
                                  "Comprueba el montaje CIFS."}, 500)
                    return
                files = sorted(
                    p.name for p in DATA_DIR.iterdir()
                    if p.is_file() and p.suffix.lower() == ".csv"
                )
                self._send_json(files)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # --- API: contenido de un CSV ---
        if sub.startswith("/data/"):
            name = unquote(sub[len("/data/"):])
            if not name or "/" in name or "\\" in name or ".." in name:
                self.send_error(403, "Ruta no permitida")
                return
            target = DATA_DIR / name
            if not target.is_file():
                self.send_error(404, f"No existe {name}")
                return
            self._send_file(target, "text/csv; charset=utf-8")
            return

        # --- Proyecto estatico ---
        # Sirve cualquier ruta /analizadores/<archivo> desde PROJECT_DIR.
        rel = sub.lstrip("/")
        if not rel or rel.endswith("/"):
            rel += "analizadores.html"
        candidate = (PROJECT_DIR / rel).resolve()
        try:
            candidate.relative_to(PROJECT_DIR)
        except ValueError:
            self.send_error(403, "Fuera del proyecto")
            return
        if not candidate.is_file():
            self.send_error(404, f"No existe {rel}")
            return

        ext = candidate.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg":  "image/svg+xml",
            ".ico":  "image/x-icon",
            ".woff2":"font/woff2",
        }.get(ext, "application/octet-stream")
        self._send_file(candidate, ctype)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def main():
    os.chdir(PROJECT_DIR)
    print("=" * 60)
    print(" Panel Cudeca - servidor local (Ubuntu)")
    print("=" * 60)
    print(f" Proyecto : {PROJECT_DIR}")
    print(f" Datos    : {DATA_DIR}  ({'OK' if DATA_DIR.exists() else 'NO MONTADO'})")
    print(f" URL      : http://{HOST}:{PORT}{URL_PREFIX}/analizadores.html")
    print("=" * 60)
    print(" Pulsa Ctrl+C para detener.")
    print()
    with ReusableTCPServer((HOST, PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor detenido.")


if __name__ == "__main__":
    main()
