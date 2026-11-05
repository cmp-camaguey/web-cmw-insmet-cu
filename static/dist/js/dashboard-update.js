/*
 * Actualizacion del servidor en el navbar del dashboard
 * (change 025-update-web-deploy).
 *
 * Consulta `dashboard:update_status` y pinta el aviso. El boton NO despliega:
 * escribe una peticion que recoge una unidad systemd en el servidor. Por eso
 * aqui solo hay fetch y texto.
 *
 * Vive en el layout del dashboard, no en una pagina: el navbar se renderiza en
 * todas, asi que el aviso aparece donde este el admin. Si el nodo no existe
 * (usuario que no es superusuario), este archivo no hace absolutamente nada.
 *
 * Sin bundler y sin dependencias: JS vanilla, como el resto del proyecto.
 */
(function () {
  'use strict';

  var POLL_IDLE_MS = 60000; // el estado cambia como mucho una vez por release
  var POLL_BUSY_MS = 2000; // mientras el servidor esta desplegando

  var root = document.getElementById('update-navbar');
  if (!root) {
    return;
  }

  var icon = document.getElementById('update-navbar-icon');
  var badge = document.getElementById('update-navbar-badge');
  var title = document.getElementById('update-navbar-title');
  var text = document.getElementById('update-navbar-text');
  var detail = document.getElementById('update-navbar-detail');
  var button = document.getElementById('update-navbar-button');

  var statusUrl = root.getAttribute('data-status-url');
  var requestUrl = root.getAttribute('data-request-url');
  var csrfToken = root.getAttribute('data-csrf-token');

  var timer = null;

  function schedule(delay) {
    window.clearTimeout(timer);
    timer = window.setTimeout(load, delay);
  }

  function paint(options) {
    // `badge` es la senal: un punto rojo en el navbar. El resto del texto vive
    // dentro del desplegable, que solo se ve si el admin lo abre.
    badge.className =
      'badge badge-pill position-absolute top-0 start-100 translate-middle ' +
      (options.badge ? 'bg-' + options.badge : 'd-none');
    badge.classList.toggle('d-none', !options.badge);

    icon.className = 'icon ' + (options.icon || 'ti ti-refresh');
    title.textContent = options.title || 'Actualizaciones';
    text.textContent = options.message || '';
    detail.textContent = options.detail || '';

    button.classList.toggle('d-none', !options.action);
    button.disabled = !options.action || Boolean(options.busy);
  }

  function render(state) {
    var run = state.last_run || {};

    if (state.state === 'running') {
      paint({
        badge: 'blue',
        icon: 'ti ti-loader',
        title: 'Desplegando',
        message: run.message || 'Desplegando…',
        detail: 'El servidor se está reiniciando. La página puede no responder unos segundos.',
        busy: true,
      });
      return;
    }

    if (state.state === 'available') {
      paint({
        badge: 'red',
        icon: 'ti ti-download',
        title: 'Actualización disponible',
        message: state.deployed_short + ' → ' + state.latest_short,
        detail: 'Instala el último commit de main en este servidor.',
        action: true,
      });
      return;
    }

    if (run.status === 'failed') {
      paint({
        badge: 'red',
        icon: 'ti ti-alert-triangle',
        title: 'El despliegue falló',
        message: run.message || 'El despliegue falló.',
        detail: 'Revisa el log del servidor. El commit anterior sigue sirviendo.',
      });
      return;
    }

    if (run.status === 'ok') {
      paint({
        icon: 'ti ti-check',
        title: 'Despliegue completado',
        message: run.message || 'Despliegue completado.',
        detail: 'Ahora se está sirviendo ' + (run.sha ? run.sha.slice(0, 7) : 'el nuevo commit') + '.',
      });
      return;
    }

    // `up_to_date` y `unknown` no se anuncian con un punto: un navbar que grita
    // "todo al día" en cada carga es ruido, y "no se pudo comprobar" sin
    // contexto solo asusta. El texto queda dentro del desplegable, para quien
    // lo abra a preguntar.
    if (state.state === 'up_to_date') {
      paint({
        icon: 'ti ti-check',
        message: 'El servidor está al día (' + (state.deployed_short || '—') + ').',
      });
      return;
    }

    paint({
      icon: state.check_error ? 'ti ti-cloud-off' : 'ti ti-help-circle',
      title: state.check_error ? 'Sin conexión con el repositorio' : 'Sin datos del despliegue',
      message: state.check_error || state.reason || 'No se pudo comprobar si hay actualizaciones.',
      detail: state.check_error
        ? 'El aviso vuelve solo cuando el repositorio responda.'
        : 'En desarrollo se simula con UPDATE_DEPLOYED_SHA_FILE; en el servidor lo escribe deploy.sh.',
    });
  }

  function load() {
    window
      .fetch(statusUrl, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) {
          throw new Error('status ' + response.status);
        }
        return response.json();
      })
      .then(function (state) {
        render(state);
        schedule(state.state === 'running' ? POLL_BUSY_MS : POLL_IDLE_MS);
      })
      .catch(function () {
        // Un fallo de red no rompe el dashboard: se reintenta en el proximo ciclo.
        schedule(POLL_IDLE_MS);
      });
  }

  function requestUpdate() {
    button.disabled = true;
    window
      .fetch(requestUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken, Accept: 'application/json' },
      })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          detail.textContent = result.body.reason || 'No se pudo pedir la actualización.';
          button.disabled = false;
          schedule(POLL_IDLE_MS);
          return;
        }
        // Aceptada: systemd tarda en arrancar la unidad, asi que se consulta en
        // el ritmo rapido hasta que el estado pase a `running`.
        detail.textContent = 'Petición enviada. Esperando al servidor…';
        schedule(POLL_BUSY_MS);
      })
      .catch(function () {
        detail.textContent = 'No se pudo contactar al servidor.';
        button.disabled = false;
        schedule(POLL_IDLE_MS);
      });
  }

  if (button) {
    button.addEventListener('click', requestUpdate);
  }

  load();
})();
