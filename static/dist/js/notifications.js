/*
 * Marcar una notificacion como leida al abrirla (cambio 026).
 *
 * El desplegable del navbar solo lista las NO leidas, asi que abrir una tiene
 * que sacarla de ahi. Se hace con un POST de fondo y se deja navegar al enlace
 * de siempre: convertir cada fila en un formulario habria funcionado sin JS,
 * pero un formulario no se abre con ctrl+clic ni con el boton central, y abrir
 * la factura o la tarea en otra pestaña es lo normal en un panel.
 *
 * Sin JS, el enlace sigue funcionando: solo no se marca leida, igual que antes
 * de este archivo. La pagina de historial tiene su boton explicito.
 */
(function () {
  'use strict';

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

  function reload() {
    window.location.reload();
  }
})();
