/*
 * Contador de avisos sin leer del navbar (cambio 026).
 *
 * Antes este numero viajaba dentro del JSON de estado de despliegue, y ahi
 * sobraba por dos motivos: el estado de despliegue lee archivos y consulta el
 * remoto en cada peticion —caro para un `COUNT` indexado— y su intervalo (60 s)
 * es el de un release, no el de un aviso. Ahora es un endpoint propio que
 * responde `{"unread": n}` o `304` si nada cambio.
 *
 * El badge llega pintado desde el servidor: sin JS el numero es correcto al
 * cargar la pagina y solo deja de refrescarse. Este archivo agrega el refresco,
 * no el dato.
 */
(function () {
  'use strict';

  var DEFAULT_INTERVAL_MS = 20000;

  var root = document.getElementById('notifications-navbar');
  if (!root) {
    return;
  }

  var url = root.getAttribute('data-unread-url');
  var badge = document.getElementById('notifications-navbar-badge');
  if (!url || !badge) {
    return;
  }

  var link = root.querySelector('.nav-link');
  var interval = parseInt(root.getAttribute('data-unread-interval'), 10);
  if (!(interval > 0)) {
    interval = DEFAULT_INTERVAL_MS;
  }

  var timer = null;
  // Ultimo ETag servido. Se guarda aqui y no se confia en la cache HTTP: el
  // navegador puede descartarla, y el `304` solo sirve si mandamos el mismo.
  var etag = null;
  var stopped = false;

  function paint(unread) {
    var count = unread || 0;
    badge.textContent = count > 9 ? '9+' : String(count);
    badge.classList.toggle('d-none', count === 0);

    if (link) {
      link.setAttribute(
        'aria-label',
        count
          ? 'Notificaciones del sistema (' + count + ' sin leer)'
          : 'Notificaciones del sistema'
      );
    }
  }

  function stop() {
    // Un 403 significa que la sesion se cerro en otra pestaña. Seguir
    // preguntando solo llena el log del servidor de 403.
    stopped = true;
    window.clearTimeout(timer);
  }

  function schedule() {
    window.clearTimeout(timer);
    if (stopped) {
      return;
    }
    timer = window.setTimeout(refresh, interval);
  }

  function refresh() {
    if (stopped) {
      return;
    }
    if (document.hidden) {
      // Pestaña oculta: no se pregunta. Es la mayor parte del ahorro con
      // muchos clientes, y no cuesta nada porque al volver se consulta igual.
      schedule();
      return;
    }

    var headers = { Accept: 'application/json' };
    if (etag) {
      headers['If-None-Match'] = etag;
    }

    fetch(url, {
      method: 'GET',
      headers: headers,
      credentials: 'same-origin',
      // `no-cache` fuerza la revalidacion y conserva la respuesta para que el
      // navegador pueda repetir el `If-None-Match`. `no-store` lo impediria.
      cache: 'no-cache',
    })
      .then(function (response) {
        if (response.status === 403 || response.status === 401) {
          stop();
          return null;
        }
        if (response.status === 304) {
          return null;
        }
        if (!response.ok) {
          return null;
        }
        etag = response.headers.get('ETag') || etag;
        return response.json();
      })
      .then(function (payload) {
        if (!payload) {
          return;
        }
        if (payload.unread === null) {
          // Sistema apagado en el servidor: no hay numero que mostrar y no
          // tiene sentido seguir preguntando.
          badge.classList.add('d-none');
          stop();
          return;
        }
        paint(payload.unread);
      })
      .catch(function () {
        // Sin red no se toca el badge: el numero viejo es mas util que un
        // hueco, y el siguiente intento lo corrige.
      })
      .then(schedule);
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden || stopped) {
      return;
    }
    // Al volver a la pestaña se pregunta de inmediato: el usuario esta
    // mirando, y esperar el intervalo completo haria que el badge pareciera
    // atrasado justo cuando importa.
    window.clearTimeout(timer);
    refresh();
  });

  schedule();
})();
