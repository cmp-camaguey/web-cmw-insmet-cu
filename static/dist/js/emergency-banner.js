/*
 * Botón de cerrar de la franja de aviso vigente.
 *
 * La memoria es por aviso, no global: `localStorage` guarda una lista con el
 * `uuid` de cada aviso cerrado junto con su `valid_until`. Si el operador edita
 * el aviso y le mueve la fecha de vigencia, la franja vuelve a aparecer, porque
 * es un hecho nuevo. Un "cerrar todo para siempre" haría que un ciclón
 * posterior no se viera nunca.
 *
 * La lista, y no un solo objeto, es lo que permite encadenar avisos: cerrar el
 * más grave no esconde el siguiente. Al cerrar uno se muestra el primero que
 * quede sin cerrar; si no queda ninguno, la franja desaparece.
 *
 * El estado inicial (qué franja se ve antes del primer paint) NO lo pone este
 * archivo: lo pone un script en línea dentro del include, porque un archivo
 * diferido llega después de que el navegador ya pintó la franja y se vería el
 * parpadeo. Aquí solo vive la interacción.
 */
(function () {
	'use strict';

	var STORAGE_KEY = 'cmp.emergencyBanner.dismissed';
	/* Tope de entradas guardadas. Son avisos cerrados, no un historial: sin tope
	   la lista crecería para siempre en el navegador de cada visitante. */
	var MAX_ENTRIES = 10;

	function readDismissed() {
		var stored;
		try {
			stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]');
		} catch (error) {
			return [];
		}
		if (!stored) {
			return [];
		}
		/* El formato anterior era un único objeto; se acepta para no resucitar
		   los avisos que el visitante ya había cerrado. */
		if (!Array.isArray(stored)) {
			stored = [stored];
		}
		return stored.filter(function (entry) {
			return entry && entry.id;
		});
	}

	function saveDismissed(entries) {
		try {
			window.localStorage.setItem(
				STORAGE_KEY,
				JSON.stringify(entries.slice(-MAX_ENTRIES))
			);
		} catch (error) {
			// Almacenamiento deshabilitado (modo privado, política del navegador):
			// la franja se oculta en esta página y volverá en la siguiente. Es
			// preferible a fallar delante de una alerta vigente.
		}
	}

	function entryOf(banner) {
		return {
			id: banner.getAttribute('data-warning-id'),
			until: banner.getAttribute('data-warning-valid-until')
		};
	}

	function remember(banner) {
		var entry = entryOf(banner);
		/* Se reemplaza por `id` y no por `id|until`: si la vigencia cambió, la
		   entrada vieja del mismo aviso ya no sirve para nada. */
		var entries = readDismissed().filter(function (other) {
			return other.id !== entry.id;
		});
		entries.push(entry);
		saveDismissed(entries);
	}

	/* Muestra la primera franja que el visitante no haya cerrado y esconde el
	   resto. Devuelve la elegida, o `null` si cerró todas. Se usa al cargar
	   (por si el script en línea no llegó a correr) y al cerrar una. */
	function select() {
		var dismissed = {};
		readDismissed().forEach(function (entry) {
			dismissed[entry.id + '|' + entry.until] = true;
		});

		var banners = document.querySelectorAll('[data-emergency-banner]');
		var shown = null;
		for (var i = 0; i < banners.length; i++) {
			var banner = banners[i];
			var closed = dismissed[
				banner.getAttribute('data-warning-id') + '|' +
				banner.getAttribute('data-warning-valid-until')
			];
			if (!shown && !closed) {
				banner.removeAttribute('hidden');
				shown = banner;
			} else {
				banner.setAttribute('hidden', 'hidden');
			}
		}
		document.documentElement.classList.toggle('emergency-banner-dismissed', !shown);
		return shown;
	}

	function focusBody() {
		var body = document.body;
		if (!body) {
			return;
		}
		// El botón desaparece con la franja: sin esto el foco cae al `body` y
		// quien navega con teclado pierde el punto donde estaba.
		if (!body.hasAttribute('tabindex')) {
			body.setAttribute('tabindex', '-1');
		}
		body.focus({ preventScroll: true });
	}

	function init() {
		var banners = document.querySelectorAll('[data-emergency-banner]');
		if (!banners.length) {
			return;
		}
		for (var i = 0; i < banners.length; i++) {
			(function (banner) {
				var button = banner.querySelector('[data-emergency-banner-close]');
				if (!button) {
					return;
				}
				button.addEventListener('click', function () {
					remember(banner);
					var next = select();
					// Si queda otra franja, el foco va a su botón de cerrar; si no
					// queda ninguna, cae al `body`.
					if (next) {
						var nextButton = next.querySelector('[data-emergency-banner-close]');
						if (nextButton) {
							nextButton.focus();
							return;
						}
					}
					focusBody();
				});
			})(banners[i]);
		}
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', init);
	} else {
		init();
	}
})();
