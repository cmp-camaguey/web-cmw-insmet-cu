/*
 * UI de notificaciones del panel (cambio 026).
 *
 * Dos cosas independientes, y por eso el archivo no sale temprano si falta una:
 *
 * 1. El desplegable del navbar solo lista las NO leidas, asi que abrir una tiene
 *    que sacarla de ahi. Se hace con un POST de fondo y se deja navegar al
 *    enlace de siempre: convertir cada fila en un formulario habria funcionado
 *    sin JS, pero un formulario no se abre con ctrl+clic ni con el boton
 *    central, y abrir la factura o la tarea en otra pestaña es lo normal en un
 *    panel. Sin JS el enlace sigue funcionando: solo no se marca leida.
 *
 * 2. El historial confirma el borrado con un modal compartido, igual que el
 *    resto del sitio. Cada boton trae su accion y su mensaje en `data-*`; al
 *    abrir el modal se copian al formulario del pie.
 */
(function () {
  'use strict';

  // El modal de borrado vive en `{% block modal %}` del layout base, y ese
  // bloque se renderiza DESPUES del bloque de pagina donde se carga este
  // archivo. Al ejecutarse el script el nodo todavia no existe, `initDeleteModal`
  // salia temprano y el formulario quedaba con `action=""`: al confirmar, el
  // navegador hacia POST a la pagina del historial, que solo acepta GET, y el
  // usuario veia un 405. `DOMContentLoaded` llega con el documento completo y,
  // de paso, despues de los `defer` del layout (tabler.min.js), asi que
  // Bootstrap ya esta listo.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  function init() {
    initReadOnClick();
    initDeleteModal();
  }

  function initReadOnClick() {
    var root = document.getElementById('notifications-navbar');
    if (!root) {
      return;
    }

    var csrfToken = root.getAttribute('data-csrf-token');

    root.addEventListener('click', function (event) {
      var item = event.target.closest('[data-notification-read]');
      if (!item) {
        return;
      }

      // Clic con modificador o boton distinto del principal: el usuario esta
      // abriendo en otra pestaña o ventana. No lo leyo todavia, no se marca.
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }

      var readUrl = item.getAttribute('data-notification-read');
      if (!readUrl) {
        return;
      }

      // `keepalive` deja que la peticion termine aunque el enlace navegue en el
      // acto. Sin `preventDefault` para el destino normal: el navegador sigue
      // siendo el que navega.
      var request = window.fetch(readUrl, {
        method: 'POST',
        credentials: 'same-origin',
        keepalive: true,
        headers: {
          'X-CSRFToken': csrfToken,
          'X-Requested-With': 'fetch',
          Accept: 'application/json',
        },
      });

      // Un aviso sin destino no navega: se recarga para que salga de la lista.
      // Con destino, la navegacion ya trae la pagina nueva.
      if (item.getAttribute('href') === '#') {
        event.preventDefault();
        request.then(reload, reload);
      }
    });
  }

  function initDeleteModal() {
    var modal = document.getElementById('notification-delete-modal');
    var form = document.getElementById('notification-delete-form');
    if (!modal || !form) {
      return;
    }

    var text = document.getElementById('notification-delete-text');
    var submit = form.querySelector('button[type="submit"]');

    modal.addEventListener('show.bs.modal', function (event) {
      var trigger = event.relatedTarget;
      if (!trigger) {
        return;
      }
      var url = trigger.getAttribute('data-delete-url') || '';
      form.setAttribute('action', url);
      // El boton nace deshabilitado: sin accion no hay nada que enviar, y
      // habilitarlo es la unica forma de que el submit llegue a la vista de
      // borrado en vez de a la pagina actual.
      if (submit) {
        submit.disabled = !url;
      }
      if (text) {
        text.textContent =
          trigger.getAttribute('data-delete-message') || '¿Eliminar esta notificación?';
      }
    });

    // Sin accion no se envia: el formulario heredaria la URL actual y haria un
    // POST a la pagina del historial, que no sabe borrar nada.
    form.addEventListener('submit', function (event) {
      if (!form.getAttribute('action')) {
        event.preventDefault();
      }
    });
  }

  function reload() {
    window.location.reload();
  }
})();
