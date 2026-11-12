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
  var commitsBox = document.getElementById('update-navbar-commits');

  var statusUrl = root.getAttribute('data-status-url');
  var requestUrl = root.getAttribute('data-request-url');
  var csrfToken = root.getAttribute('data-csrf-token');

  var timer = null;

  function schedule(delay) {
    window.clearTimeout(timer);
    timer = window.setTimeout(load, delay);
  }

  function clearCommits() {
    while (commitsBox.firstChild) {
      commitsBox.removeChild(commitsBox.firstChild);
    }
    commitsBox.classList.add('d-none');
  }

  function paintCommits(state) {
    // Un template viejo en cache (o una pagina que no incluye la lista) deja
    // `commitsBox` en null. Sin esta guarda, `clearCommits` lanzaria
    // `TypeError` y se llevaria por delante TODO el render del aviso, no solo
    // la lista: el admin perderia el aviso de actualizacion entero.
    if (!commitsBox) {
      return;
    }

    clearCommits();

    var payload = state.commits;
    if (!payload || !payload.commits || !payload.commits.length) {
      // Sin mensajes se cae a los SHA que ya pinta `render`, y si tampoco hay
      // eso, a nada. Un fallo de la API no se anuncia: el aviso de
      // actualizacion sigue siendo cierto.
      return;
    }

    payload.commits.forEach(function (entry) {
      var item = document.createElement('li');
      item.className = 'px-3 py-1';

      var row = document.createElement('div');
      row.className = 'd-flex align-items-start gap-2';

      var sha = document.createElement('span');
      sha.className = 'text-secondary small font-monospace flex-shrink-0';
      sha.textContent = entry.sha || '·······';

      // `textContent` y no `innerHTML`: el mensaje viene de un commit, o sea de
      // cualquiera que pueda pushear a main. Un `<img onerror>` ahi dentro se
      // ejecutaria en el panel del administrador.
      var subject = document.createElement('span');
      subject.className = 'text-truncate small flex-grow-1';
      // Sin `min-width: 0` un flex item no baja de su ancho de contenido y
      // `text-truncate` no recorta nada: el mensaje largo desborda el menu.
      subject.style.minWidth = '0';
      subject.textContent = entry.subject || '(sin mensaje)';
      subject.title = entry.subject || '';

      row.appendChild(sha);
      row.appendChild(subject);
      item.appendChild(row);
      commitsBox.appendChild(item);
    });

    if (payload.truncated) {
      var rest = payload.total - payload.commits.length;
      var more = document.createElement('li');
      more.className = 'px-3 py-1 text-secondary small';
      more.textContent = 'y ' + rest + ' commit' + (rest === 1 ? '' : 's') + ' más';
      commitsBox.appendChild(more);
    }

    commitsBox.classList.remove('d-none');
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
    // Antes de cualquier pintado: si el estado dejo de ser `available`, la
    // lista vieja tiene que desaparecer, no quedarse pegada al desplegable.
    paintCommits(state);

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
      // `state.state === 'failed'` es lo que responde el backend cuando el
      // intento fallido fue sobre el commit que sigue pendiente: ahi el boton
      // sirve para reintentar, que es lo que hace falta despues de arreglar la
      // causa. En cualquier otro caso el fallo es historia (el servidor ya
      // quedo al dia por otra via) y ofrecer el boton mandaria a desplegar lo
      // que ya se esta sirviendo.
      var retry = state.state === 'failed';
      paint({
        badge: 'red',
        icon: 'ti ti-alert-triangle',
        title: 'El despliegue falló',
        message: run.message || 'El despliegue falló.',
        detail: retry
          ? 'Revisa el log del servidor. El commit anterior sigue sirviendo; puedes reintentar.'
          : 'Revisa el log del servidor. El commit anterior sigue sirviendo.',
        action: retry,
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
