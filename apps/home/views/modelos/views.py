import base64
import io
import ipaddress
import json
import logging
import re
import socket
from datetime import datetime
from urllib.parse import unquote, urlencode, urlparse

import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView
from PIL import Image

from apps.core.utils import rate_limit_ip
from apps.home.forms import GifDownloadForm, MeteoDataForm, MeteogramForm, SoundingForm
from apps.meteo.models import Town

# Configurar logger
logger = logging.getLogger(__name__)

# API externa de modelos: entrega el Skew-T + hodógrafo ya dibujado.
SOUNDING_API_URL = 'https://modelo.cmw.insmet.cu/api/sounding/'

SOUNDING_EXPORT_CONTENT_TYPES = {
    'png': 'image/png',
    'pdf': 'application/pdf',
    'svg': 'image/svg+xml',
}


class SoundingAPIError(Exception):
    """Fallo al obtener el gráfico de sondeo de la API externa de modelos."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _decode_sounding_json(data):
    """Extraer ``(plot_bytes, datetime)`` del sobre JSON de la API de sondeo."""
    if not isinstance(data, dict) or data.get('status') == 'error':
        raise SoundingAPIError('La API de sondeo no respondió correctamente.')

    raw_plot = data.get('plot_image') or data.get('image')
    if not isinstance(raw_plot, str) or not raw_plot:
        raise SoundingAPIError('La API de sondeo no devolvió ningún gráfico.')

    # La API puede entregar un data URI (`data:image/png;base64,...`) o solo el base64.
    payload = raw_plot.split(',', 1)[1] if raw_plot.startswith('data:') else raw_plot
    try:
        return base64.b64decode(payload), data.get('datetime')
    except ValueError:
        raise SoundingAPIError('La imagen devuelta por la API de sondeo no es válida.') from None


def fetch_sounding_plot(params):
    """Pedir a la API externa el Skew-T + hodógrafo y devolver ``(bytes, datetime)``.

    La API puede responder el gráfico en crudo (``image/*``) o un sobre JSON con el
    gráfico en base64 (``plot_image`` / ``image``). Los errores de upstream se
    traducen a ``SoundingAPIError`` con el status HTTP que corresponde al usuario.
    """
    try:
        response = requests.get(
            f'{SOUNDING_API_URL}?{urlencode(params)}',
            timeout=30,
            verify=False,  # nosec B501
        )
    except requests.RequestException as error:
        raise SoundingAPIError(
            'No se pudo conectar con la API de sondeo en este momento.'
        ) from error

    if response.status_code == 404:
        raise SoundingAPIError(
            'No hay datos de sondeo disponibles para los parámetros seleccionados.',
            status=404,
        )

    try:
        response.raise_for_status()
    except requests.RequestException as error:
        raise SoundingAPIError('La API de sondeo no respondió correctamente.') from error

    if 'json' not in response.headers.get('Content-Type', '').lower():
        if not response.content:
            raise SoundingAPIError('La API de sondeo no devolvió ningún gráfico.')
        return response.content, None

    return _decode_sounding_json(response.json())


class MapaView(TemplateView):
    template_name = 'pages/home/models/maps.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Establecer valores por defecto
        initial_data = {'datetime_init': self.get_default_datetime(), 'var_name': 'T2'}

        # Obtener parámetros de la URL si existen
        fecha_inicio_url = self.request.GET.get('fecha_inicio')
        fecha_fin_url = self.request.GET.get('fecha_fin')

        # Formulario para GIF con valores iniciales
        gif_initial = {}
        if fecha_inicio_url:
            gif_initial['fecha_inicio'] = fecha_inicio_url
        if fecha_fin_url:
            gif_initial['fecha_fin'] = fecha_fin_url

        context['form'] = MeteoDataForm(initial=initial_data)
        context['gif_form'] = GifDownloadForm(initial=gif_initial)  # Nuevo formulario para GIF
        context['initial_date'] = self.get_default_date()
        context['title'] = 'Modelo de pronóstico WRF'
        context['parent'] = 'modelos'
        context['segment'] = 'models_maps'
        return context

    def get_default_datetime(self):
        """Obtener datetime_init por defecto (fecha actual a las 00:00)"""
        now = datetime.now()
        return now.strftime('%Y%m%d00')

    def get_default_date(self):
        """Obtener fecha por defecto para el datepicker"""
        now = datetime.now()
        return now.strftime('%d/%m/%Y')

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {'status': 'error', 'message': 'Datos inválidos en la solicitud'}, status=400
            )

        form = MeteoDataForm(data)

        if form.is_valid():
            api_result = self.fetch_image_urls(
                form.cleaned_data['datetime_init'], form.cleaned_data['var_name']
            )

            if api_result['status'] == 'error':
                # Mejorar el mensaje de error para el usuario
                error_message = self.parse_api_error(api_result['message'])
                return JsonResponse({'status': 'error', 'message': error_message}, status=500)

            return JsonResponse(
                {
                    'status': 'success',
                    'datetime_init': form.cleaned_data['datetime_init'],
                    'var_name': form.cleaned_data['var_name'],
                    'var_label': dict(MeteoDataForm.VAR_CHOICES).get(form.cleaned_data['var_name']),
                    'image_urls': api_result['image_urls'],
                    'simulation_date': api_result.get('simulation_date'),
                    'count': api_result.get('count'),
                }
            )

        # Mejorar los errores de validación del formulario
        errors = self.format_form_errors(form.errors.get_json_data())
        return JsonResponse({'status': 'error', 'message': errors}, status=400)

    def parse_api_error(self, error_message):
        """Parsear y mejorar los mensajes de error de la API"""
        if '404' in error_message and 'Not Found' in error_message:
            return (
                'No se encontraron datos para los parámetros seleccionados. '
                'Por favor, intente con otra fecha o variable.'
            )
        elif 'ConnectionError' in error_message or 'Timeout' in error_message:
            return (
                'Error de conexión con el servidor de datos. '
                'Por favor, intente nuevamente en unos momentos.'
            )
        elif '500' in error_message:
            return 'Error interno del servidor. Por favor, contacte al administrador.'
        else:
            # Para otros errores, devolver un mensaje genérico sin detalles técnicos
            return (
                'No se pudieron cargar los datos. '
                'Por favor, verifique los parámetros e intente nuevamente.'
            )

    def format_form_errors(self, errors_dict):
        """Formatear errores del formulario para mostrarlos al usuario"""
        error_messages = []
        for field, errors in errors_dict.items():
            for error in errors:
                error_messages.append(f'{field}: {error["message"]}')
        return '; '.join(error_messages)

    def fetch_image_urls(self, datetime_init, var_name):
        """Obtener URLs de imágenes de la nueva API"""
        try:
            api_url = f'http://apimet.cmw.insmet.cu/api/simulations/?datetime_init={datetime_init}&var_name={var_name}'
            response = requests.get(api_url, timeout=30, verify=False)  # nosec B501
            response.raise_for_status()
            data = response.json()

            if data.get('status') != 'success':
                error_msg = data.get('message', 'Error desconocido en la API')
                raise ValueError(f'API error: {error_msg}')

            return {
                'status': 'success',
                'image_urls': data.get('image_urls', []),
                'simulation_date': data.get('simulation_date'),
                'count': data.get('count', 0),
            }

        except requests.exceptions.ConnectionError:
            return {
                'status': 'error',
                'message': 'ConnectionError: No se pudo conectar al servidor de datos',
            }
        except requests.exceptions.Timeout:
            return {
                'status': 'error',
                'message': 'Timeout: La conexión con el servidor tardó demasiado',
            }
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return {
                    'status': 'error',
                    'message': '404: No se encontraron datos para los parámetros solicitados',
                }
            else:
                return {
                    'status': 'error',
                    'message': f'HTTPError {e.response.status_code}: Error del servidor',
                }
        except Exception:
            logger.exception('Unhandled exception in fetch_image_urls')
            return {
                'status': 'error',
                'message': 'Ocurrió un error interno al procesar la solicitud.',
            }


class MeteogramView(TemplateView):
    template_name = 'pages/home/models/meteogram.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Meteorama'
        context['parent'] = 'modelos'
        context['segment'] = 'models_meteogram'

        # Obtener municipio por defecto
        default_town = Town.objects.filter(latitude=21.391, longitude=-77.908).first()

        initial = {
            'datetime_init': self.request.GET.get(
                'datetime_init', f'{datetime.now().strftime("%Y%m%d")}00'
            ),
            'town': default_town.id if default_town else None,
        }
        context['form'] = MeteogramForm(initial=initial)
        return context

    def post(self, request, *args, **kwargs):
        form = MeteogramForm(request.POST)
        if not form.is_valid():
            return JsonResponse(
                {
                    'status': 'error',
                    'message': 'Datos del formulario inválidos',
                    'errors': form.errors.get_json_data(),
                },
                status=400,
            )

        try:
            # Obtener el municipio seleccionado
            town = form.cleaned_data['town']
            datetime_init = form.cleaned_data['datetime_init']

            # Validar que la fecha no sea futura
            datetime_init = form.cleaned_data['datetime_init']
            init_date = datetime.strptime(datetime_init, '%Y%m%d%H')
            if init_date > datetime.now():
                return JsonResponse(
                    {
                        'status': 'error',
                        'message': 'No se pueden solicitar datos para fechas futuras',
                    },
                    status=400,
                )

            # Construir URL usando coordenadas del municipio
            params = {
                'datetime_init': datetime_init,
                'lat': town.latitude,
                'long': town.longitude,
            }
            api_url = f'https://modelo.cmw.insmet.cu/api/meteogram/?{urlencode(params)}'

            response = requests.get(api_url, timeout=10, verify=False)  # nosec B501
            response.raise_for_status()
            api_data = response.json()

            if not isinstance(api_data, dict) or 'times' not in api_data:
                return JsonResponse(
                    {'status': 'error', 'message': 'Estructura de datos inesperada de la API'},
                    status=500,
                )

            # Validar que hay datos disponibles
            if not api_data.get('times') or len(api_data['times']) == 0:
                return JsonResponse(
                    {
                        'status': 'error',
                        'message': (
                            'No hay datos disponibles para la fecha y ubicación seleccionadas'
                        ),
                    },
                    status=404,
                )

            return JsonResponse({'status': 'success', 'data': api_data})

        except requests.RequestException:
            logger.exception('Error al conectar con la API en MeteogramView:')
            return JsonResponse(
                {
                    'status': 'error',
                    'message': 'No se pudo conectar con la API de meteogramas en este momento.',
                },
                status=500,
            )
        except Exception:
            logger.exception('Error interno del servidor en MeteogramView:')
            return JsonResponse(
                {'status': 'error', 'message': 'Error interno del servidor'}, status=500
            )


class SoundingView(TemplateView):
    template_name = 'pages/home/models/sounding.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Sondeos'
        context['parent'] = 'modelos'
        context['segment'] = 'models_sounding'

        # Obtener municipio por defecto (ej. usando coordenadas predeterminadas)
        default_town = Town.objects.filter(latitude=21.3786, longitude=-77.9186).first()

        initial = {
            'datetime_init': self.request.GET.get(
                'datetime_init', f'{datetime.now().strftime("%Y%m%d")}00'
            ),
            'town': default_town.id if default_town else None,
            't_index': int(self.request.GET.get('t_index', 0)),
        }
        context['form'] = SoundingForm(initial=initial)
        return context

    def post(self, request, *args, **kwargs):
        form = SoundingForm(request.POST)
        if not form.is_valid():
            return JsonResponse(
                {'status': 'error', 'errors': form.errors.get_json_data()}, status=400
            )

        # Construir la petición con las coordenadas del municipio; el gráfico lo
        # dibuja la API externa, el portal solo la reenvía.
        town = form.cleaned_data['town']
        params = {
            'datetime_init': form.cleaned_data['datetime_init'],
            'lat': town.latitude,
            'long': town.longitude,
            't_index': form.cleaned_data['t_index'] - 1,
        }

        try:
            plot_bytes, sounding_datetime = fetch_sounding_plot(params)
        except SoundingAPIError as error:
            logger.warning('Sondeo no disponible: %s', error)
            return JsonResponse({'status': 'error', 'message': str(error)}, status=error.status)
        except Exception:
            logger.exception('Error interno del servidor en SoundingView:')
            return JsonResponse(
                {'status': 'error', 'message': 'Error interno del servidor'}, status=500
            )

        return JsonResponse(
            {
                'status': 'success',
                'plot_image': base64.b64encode(plot_bytes).decode('ascii'),
                'datetime': sounding_datetime,
                'params': params,
            },
            content_type='application/json',
        )


class SoundingExportView(View):
    """
    Vista para exportar el Skew-T + hodógrafo en PNG / PDF / SVG.

    El gráfico lo genera la API externa de modelos: aquí solo se reenvía el
    formato pedido y se sirve el archivo resultante como descarga.
    """

    ALLOWED_FMT = {'png', 'pdf', 'svg'}

    CONTENT_TYPES = SOUNDING_EXPORT_CONTENT_TYPES

    def get(self, request, *args, **kwargs):
        fmt = kwargs.get('fmt', '')

        # Validar formato contra la lista blanca (única frontera de seguridad)
        if fmt not in self.ALLOWED_FMT:
            return HttpResponse('Formato de exportación no soportado', status=400)

        town = Town.objects.filter(latitude=21.3786, longitude=-77.9186).first()
        if not town:
            return HttpResponse('No se pudo determinar la ubicación', status=500)

        params = {
            'datetime_init': f'{datetime.now().strftime("%Y%m%d")}00',
            'lat': town.latitude,
            'long': town.longitude,
            't_index': 0,
            'fmt': fmt,
        }

        try:
            file_bytes, _ = fetch_sounding_plot(params)
        except SoundingAPIError as error:
            logger.warning('Exportación de sondeo no disponible: %s', error)
            return HttpResponse(str(error), status=error.status)
        except Exception:
            logger.exception('Error interno al exportar el sondeo')
            return HttpResponse('Error al generar el gráfico.', status=500)

        stamp = datetime.now().strftime('%Y%m%d%H%M%S')
        filename = f'sounding_{stamp}.{fmt}'
        http_response = HttpResponse(file_bytes, content_type=self.CONTENT_TYPES[fmt])
        http_response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return http_response


@method_decorator(rate_limit_ip(limit=300, window=3600, key_prefix='rl_proxy'), name='dispatch')
class ImageProxyModeloView(View):
    """
    Vista basada en clase para proxy de imágenes que evita problemas de CORS.
    """

    # Lista blanca de dominios permitidos
    ALLOWED_DOMAINS = ['apimet.cmw.insmet.cu', 'localhost', '127.0.0.1']

    def get(self, request, *args, **kwargs):
        image_url = request.GET.get('image_url', '')
        image_path = request.GET.get('image_path', '')

        # Determinar la URL de destino
        target_url = self._get_target_url(image_url, image_path)

        if not target_url:
            return HttpResponse('URL de imagen no proporcionada', status=400)

        try:
            # Validar la URL
            if not self._is_valid_url(target_url):
                logger.warning(f'URL rechazada por validación de seguridad: {target_url}')
                return HttpResponse('URL no válida', status=400)

            # Descargar la imagen
            response = self._fetch_image(target_url)

            if response.status_code != 200:
                logger.error(f'Error {response.status_code} al obtener imagen: {target_url}')
                return HttpResponse('Error al obtener la imagen', status=response.status_code)

            # Crear la respuesta
            django_response = HttpResponse(
                response.content, content_type=response.headers.get('Content-Type', 'image/jpeg')
            )

            # Configurar headers para caching
            django_response['Cache-Control'] = 'public, max-age=3600'
            return django_response

        except requests.exceptions.RequestException as e:
            logger.error(f'Error en proxy de imagen: {str(e)} - URL: {target_url}')
            return HttpResponse('Error al obtener la imagen', status=500)
        except Exception as e:
            logger.error(f'Error inesperado en proxy de imagen: {str(e)} - URL: {target_url}')
            return HttpResponse('Error interno del servidor', status=500)

    def _get_target_url(self, image_url, image_path):
        """
        Construye la URL de destino a partir de image_url o image_path.
        Si image_path es una URL absoluta, se usa directamente.
        """
        if image_url:
            parsed = urlparse(unquote(image_url))
            if parsed.scheme not in ('http', 'https'):
                logger.warning(f'Esquema no permitido en image_url: {parsed.scheme}')
                return None
            host = parsed.hostname
            if not host or host not in self.ALLOWED_DOMAINS:
                logger.warning(f'Host no permitido en image_url: {host}')
                return None
            return parsed.geturl()

        elif image_path:
            clean_path = unquote(image_path).lstrip('/')
            # Si es una URL absoluta, tratarla como image_url
            if clean_path.startswith(('http://', 'https://')):
                parsed = urlparse(clean_path)
                if parsed.scheme not in ('http', 'https'):
                    logger.warning(f'Esquema no permitido en image_path absoluto: {parsed.scheme}')
                    return None
                host = parsed.hostname
                if not host or host not in self.ALLOWED_DOMAINS:
                    logger.warning(f'Host no permitido en image_path absoluto: {host}')
                    return None
                return clean_path
            else:
                base_url = getattr(settings, 'IMAGE_SERVER_BASE_URL', 'http://apimet.cmw.insmet.cu')
                return f'{base_url.rstrip("/")}/{clean_path}'

        return None

    def _is_valid_url(self, url):
        """
        Valida que la URL sea segura y esté permitida.
        - Esquema http/https
        - Dominio en lista blanca
        - Si el dominio está en la lista blanca, se permiten IPs privadas
        - Se bloquean IPs loopback, link-local, multicast y reservadas en cualquier caso
        """
        try:
            parsed_url = urlparse(url)
            if parsed_url.scheme not in ('http', 'https'):
                return False

            host = parsed_url.hostname
            if not host:
                return False

            # Verificar dominio en lista blanca
            if host not in self.ALLOWED_DOMAINS:
                logger.warning(f'Dominio no permitido: {host}')
                return False

            # Resolver IPs del host
            try:
                addrinfos = socket.getaddrinfo(host, None)
                for info in addrinfos:
                    ip = info[4][0]
                    ip_obj = ipaddress.ip_address(ip)
                    # Siempre bloquear direcciones peligrosas
                    if (
                        ip_obj.is_loopback
                        or ip_obj.is_link_local
                        or ip_obj.is_multicast
                        or ip_obj.is_reserved
                    ):
                        logger.warning(f'IP no permitida (incluso con dominio permitido): {ip}')
                        return False
                    # IPs privadas se permiten porque el dominio está en la lista blanca
            except Exception as e:
                logger.warning(f'Error resolviendo IP para {host}: {str(e)}')
                return False

            return True

        except Exception as e:
            logger.error(f'Error en validación de URL {url}: {str(e)}')
            return False

    def _fetch_image(self, url):
        """
        Descarga la imagen desde la URL proporcionada.
        Se realiza la petición solo a URLs previamente validadas
        y con verificación TLS habilitada.
        """
        headers = {'User-Agent': 'MeteoApp/1.0', 'Accept': 'image/*'}

        # Agregar headers de autenticación si es necesario
        auth_headers = self._get_auth_headers()
        headers.update(auth_headers)

        return requests.get(url, stream=True, timeout=30, headers=headers, verify=True)

    def _get_auth_headers(self):
        return {}


@method_decorator(rate_limit_ip(limit=60, window=3600, key_prefix='rl_gif'), name='dispatch')
class DescargarGifView(View):
    """
    Vista para descargar GIF animado de datos meteorológicos
    """

    def get(self, request, *args, **kwargs):
        # Obtener parámetros de la URL
        datetime_init = request.GET.get('datetime_init')
        var_name = request.GET.get('var_name')
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')

        # Validar parámetros obligatorios
        if not datetime_init or not var_name:
            return HttpResponse('Se requieren los parámetros datetime_init y var_name', status=400)

        # Validar formato de fecha inicial
        if not self.validar_formato_fecha(datetime_init):
            return HttpResponse('Formato de datetime_init inválido. Use YYYYMMDDHH', status=400)

        # Validar variable
        if var_name not in dict(MeteoDataForm.VAR_CHOICES):
            return HttpResponse('Variable no válida', status=400)

        # Validar rango de fechas si se proporciona
        if fecha_inicio or fecha_fin:
            if not fecha_inicio or not fecha_fin:
                return HttpResponse(
                    'Se deben proporcionar ambas fechas: fecha_inicio y fecha_fin', status=400
                )

            if not self.validar_formato_fecha(fecha_inicio) or not self.validar_formato_fecha(
                fecha_fin
            ):
                return HttpResponse('Formato de fechas inválido. Use YYYYMMDDHH', status=400)

            # Validar que fecha_inicio <= fecha_fin
            fecha_ini_dt = datetime.strptime(fecha_inicio, '%Y%m%d%H')
            fecha_fin_dt = datetime.strptime(fecha_fin, '%Y%m%d%H')
            if fecha_ini_dt > fecha_fin_dt:
                return HttpResponse(
                    'La fecha de inicio no puede ser mayor que la fecha final', status=400
                )

            # Validar rango máximo de 3 días (72 horas)
            diferencia = fecha_fin_dt - fecha_ini_dt
            if diferencia.total_seconds() > 72 * 3600:  # 72 horas en segundos
                return HttpResponse('El rango máximo permitido es de 3 días (72 horas)', status=400)

        try:
            # Construir la URL del servicio
            url = f'http://apimet.cmw.insmet.cu/api/simulations/?datetime_init={datetime_init}&var_name={var_name}'

            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Verificar si la solicitud fue exitosa
            if data.get('status') != 'success':
                logger.error(
                    'Error del servidor meteorológico. Estado devuelto: %r', data.get('status')
                )
                return HttpResponse('Error del servidor meteorológico.', status=500)

            image_urls = data.get('image_urls', [])
            if not image_urls:
                return HttpResponse(
                    'No se encontraron imágenes en la respuesta del servidor', status=404
                )

            # Filtrar imágenes por rango si se especificó
            if fecha_inicio and fecha_fin:
                image_urls = self.filtrar_imagenes_por_rango(image_urls, fecha_inicio, fecha_fin)

            if not image_urls:
                return HttpResponse('No hay imágenes en el rango especificado', status=404)

            # Validar que no haya demasiadas imágenes (máximo 25 imágenes para 3 días)
            if len(image_urls) > 25:
                return HttpResponse('Demasiadas imágenes para el rango seleccionado', status=400)

            # Lista para almacenar las imágenes PIL
            images = []
            downloaded_count = 0

            # Descargar y procesar cada imagen
            for _i, img_url in enumerate(image_urls):
                try:
                    img_response = requests.get(img_url, timeout=30)
                    img_response.raise_for_status()

                    # Abrir la imagen con PIL
                    img = Image.open(io.BytesIO(img_response.content))

                    # Convertir a RGB si es necesario
                    if img.mode in ('RGBA', 'LA', 'P'):
                        bg = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                        img = bg
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')

                    images.append(img)
                    downloaded_count += 1

                except Exception:
                    logger.exception('Error al procesar %s', img_url)
                    continue

            # Crear el GIF si hay imágenes descargadas
            if images:
                # Crear nombre descriptivo para el archivo
                if fecha_inicio and fecha_fin:
                    nombre_archivo = f'{var_name}_{fecha_inicio}_to_{fecha_fin}.gif'
                else:
                    nombre_archivo = f'{var_name}_{datetime_init}_full_range.gif'

                # Crear el GIF en memoria
                gif_buffer = io.BytesIO()
                images[0].save(
                    gif_buffer,
                    format='GIF',
                    save_all=True,
                    append_images=images[1:],
                    duration=500,
                    loop=0,
                    optimize=True,
                )
                gif_buffer.seek(0)

                # Limpiar imágenes
                for img in images:
                    img.close()

                # Cose()

                # Crear respuesta HTTP con el GIF
                response = HttpResponse(gif_buffer.getvalue(), content_type='image/gif')
                response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
                return response
            else:
                return HttpResponse('No se pudieron cargar imágenes para crear el GIF', status=500)

        except requests.exceptions.RequestException:
            logger.exception('Error de conexión al obtener datos para el GIF animado')
            return HttpResponse(
                'Error de conexión con el servidor remoto. Inténtelo de nuevo más tarde.',
                status=500,
            )
        except Exception:
            logger.exception('Error interno del servidor al generar el GIF animado')
            return HttpResponse(
                'Error interno del servidor. Inténtelo de nuevo más tarde.', status=500
            )

    def validar_formato_fecha(self, fecha_str):
        """Valida el formato de fecha YYYYMMDDHH"""
        try:
            if len(fecha_str) != 10:
                return False
            datetime.strptime(fecha_str, '%Y%m%d%H')
            return True
        except ValueError:
            return False

    def extraer_fecha_desde_url(self, url):
        """Extrae la fecha y hora desde la URL de la imagen"""
        patron = r'(\d{4}-\d{2}-\d{2})T(\d{2})-\d{2}-\d{2}'
        coincidencia = re.search(patron, url)

        if coincidencia:
            fecha_str = coincidencia.group(1)  # 2025-10-28
            hora_str = coincidencia.group(2)  # 18
            return f'{fecha_str.replace("-", "")}{hora_str}'
        return None

    def filtrar_imagenes_por_rango(self, image_urls, fecha_inicio, fecha_fin):
        """Filtra las imágenes por rango de fechas"""
        imagenes_filtradas = []

        for url in image_urls:
            fecha_imagen = self.extraer_fecha_desde_url(url)
            if fecha_imagen:
                # Convertir a objetos datetime para comparación
                fecha_img_dt = datetime.strptime(fecha_imagen, '%Y%m%d%H')
                fecha_ini_dt = datetime.strptime(fecha_inicio, '%Y%m%d%H')
                fecha_fin_dt = datetime.strptime(fecha_fin, '%Y%m%d%H')

                if fecha_ini_dt <= fecha_img_dt <= fecha_fin_dt:
                    imagenes_filtradas.append(url)

        return imagenes_filtradas
