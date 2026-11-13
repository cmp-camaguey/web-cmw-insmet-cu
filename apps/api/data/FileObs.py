import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# from werkzeug.utils import secure_filename
#
# from apps.dashboard.templatetags.my_filters import filename


class FileObs:
    # Bloqueo a nivel de clase para todas las instancias
    _download_lock = threading.Lock()

    def __init__(self):
        from django.conf import settings

        self.HOST = settings.FTP_OBS_HOST
        self.USER = settings.FTP_OBS_USER
        self.PASS = settings.FTP_OBS_PASS
        self.PORT = settings.FTP_OBS_PORT
        self.REMOTE_DIR = '/Reportes Procesados'
        # Siguen a MEDIA_ROOT para que aislen los tests y no dependan del cwd;
        # quedan como str porque el resto del archivo las usa con os.path y lftp.
        self.TEMP_DIR = str(Path(settings.MEDIA_ROOT) / 'temp')
        self.FINAL_DIR = str(Path(settings.MEDIA_ROOT) / 'obs')
        self.horas_validas = ['00', '03', '06', '09', '12', '15', '18', '21']
        self.max_retries = 3
        self.retry_delay = 5  # segundos entre reintentos

    def descargar_archivos_por_hora(self, hora, station_number):
        from django.conf import settings as django_settings

        number = str(station_number)[2:]
        # Validar hora
        if hora not in self.horas_validas:
            raise ValueError(f'Hora inválida. Usa una de: {self.horas_validas}')
        else:
            if hora in ['00', '06', '12', '18']:
                filename = f'SM{number}.{hora}'
            elif hora in ['03', '09', '15', '21']:
                filename = f'SI{number}.{hora}'
            else:
                filename = None

        # Sanitize and validate filename to prevent path traversal or dangerous characters
        if filename is not None:
            # Normalize and reduce to basename to strip any path components
            filename = os.path.basename(os.path.normpath(str(filename)))
            # Enforce strict expected pattern: prefix SM/SI, then digits, then '.', then valid hour
            pattern = r'^(SM|SI)\d+\.(00|03|06|09|12|15|18|21)$'
            if not re.fullmatch(pattern, filename):
                raise ValueError(f'Nombre de archivo inválido generado: {filename}')

        # Crear directorios (temp y final)
        os.makedirs(self.TEMP_DIR, exist_ok=True)
        os.makedirs(self.FINAL_DIR, exist_ok=True)

        # Modo local (dev/testing) y modo manual del panel: sirve el archivo
        # desde media/obs sin FTPS. En manual no hay descarga: el telegrama lo
        # escribe la observación capturada en el dashboard.
        if django_settings.OBS_LOCAL_ONLY or not getattr(django_settings, 'OBS_AUTOMATIC', True):
            local_path = os.path.normpath(os.path.join(self.FINAL_DIR, filename))
            if os.path.exists(local_path):
                logger.info('Modo local: sirviendo %s sin FTP', local_path)
                return local_path
            if getattr(django_settings, 'OBS_AUTOMATIC', True):
                raise FileNotFoundError(
                    f'Archivo local no encontrado en modo OBS_LOCAL_ONLY: {local_path}. '
                    'Ejecuta `python manage.py generate_obs` para generarlo.'
                )
            raise FileNotFoundError(
                f'No hay telegrama para {filename} en {self.FINAL_DIR}. '
                'La estación no lo ha capturado desde el panel.'
            )

        # Configuración robusta de LFTP
        comando = f"""
            set ftp:ssl-allow yes;
            set ssl:verify-certificate no;
            set ftp:ssl-protect-data yes;
            set ftp:ssl-protect-list yes;
            set ftp:ssl-force yes;
            set ftp:ssl-auth TLS;
            set net:connection-limit 1;
            set net:timeout 60;
            set xfer:clobber on;
            set net:reconnect-interval-base 15;
            set net:max-retries 2;
            cd '{self.REMOTE_DIR}';
            get {filename} -o {self.TEMP_DIR}/{filename};  # Ruta completa de destino
            bye
            """

        # Intentar la descarga con bloqueo y reintentos
        for attempt in range(self.max_retries):
            try:
                with self._download_lock:  # Bloquea el acceso concurrente
                    logger.info('Intento %d: Descargando %s...', attempt + 1, filename)

                    # Limpiar directorio temporal antes de cada intento
                    self.limpiar_directorio_temporal()
                    os.makedirs(self.TEMP_DIR, exist_ok=True)

                    # Ejecutar LFTP
                    subprocess.run(
                        [
                            'lftp',
                            '-u',
                            f'{self.USER},{self.PASS}',
                            '-p',
                            self.PORT,
                            f'ftps://{self.HOST}',
                            '-e',
                            comando,
                        ],  # FTPS explícito
                        check=True,
                        text=True,
                        capture_output=True,
                        timeout=90,
                    )

                    # Verificar si el archivo se descargó correctamente
                    temp_file = os.path.join(self.TEMP_DIR, filename)
                    if os.path.exists(temp_file):
                        # Normalize and validate the temp file path
                        temp_file_norm = os.path.normpath(os.path.abspath(temp_file))
                        temp_dir_norm = os.path.normpath(os.path.abspath(self.TEMP_DIR))
                        if not temp_file_norm.startswith(temp_dir_norm + os.sep):
                            raise Exception('Invalid temp file path: path traversal detected')
                        destino = os.path.join(self.FINAL_DIR, filename)
                        # Normalize and validate the destination path
                        destino_norm = os.path.normpath(os.path.abspath(destino))
                        final_dir_norm = os.path.normpath(os.path.abspath(self.FINAL_DIR))
                        if not destino_norm.startswith(final_dir_norm + os.sep):
                            raise Exception('Invalid file path: path traversal detected')
                        shutil.move(temp_file_norm, destino_norm)
                        logger.info('Descarga completada: %s', filename)
                        return destino_norm
                    else:
                        raise Exception(f'Archivo {filename} no se descargó correctamente')

            except subprocess.CalledProcessError as e:
                error_msg = f'❌ Error en descarga (intento {attempt + 1}): {e.stderr}'
                logger.error('Error en descarga (intento %d): %s', attempt + 1, e.stderr)
                if attempt == self.max_retries - 1:
                    raise Exception(
                        f'Fallo después de {self.max_retries} intentos. Último error: {error_msg}'
                    ) from e
                time.sleep(self.retry_delay)

            except Exception:
                logger.exception('Error inesperado en descarga')
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(self.retry_delay)

    def limpiar_directorio_temporal(self):
        """Limpia el directorio temporal de forma segura"""
        try:
            if os.path.exists(self.TEMP_DIR):
                shutil.rmtree(self.TEMP_DIR)
        except Exception:
            logger.exception('Error limpiando directorio temporal')
