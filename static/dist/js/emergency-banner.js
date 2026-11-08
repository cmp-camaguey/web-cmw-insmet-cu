/*
 * Botón de cerrar de la franja de aviso vigente.
 *
 * La memoria es por aviso, no global: `localStorage` guarda el `uuid` del aviso
 * junto con su `valid_until`. Si el operador edita el aviso y le mueve la fecha
 * de vigencia, la franja vuelve a aparecer, porque es un hecho nuevo. Un
 * "cerrar todo para siempre" haría que un ciclón posterior no se viera nunca.
 *
 * El estado inicial (franja oculta antes del primer paint) NO lo pone este
 * archivo: lo pone un script en línea dentro del propio include, porque un
 * archivo diferido llega después de que el navegador ya pintó la franja y se
 * vería el parpadeo. Aquí solo vive la interacción.
 */
(function () {
	'use strict';

	var STORAGE_KEY = 'cmp.emergencyBanner.dismissed';

	function remember(banner) {
		var payload = {
			id: banner.getAttribute('data-warning-id'),
			until: banner.getAttribute('data-warning-valid-until')
		};
		try {
			window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
		} catch (error) {
			// Almacenamiento deshabilitado (modo privado, política del navegador):
			// la franja se oculta en esta página y volverá en la siguiente. Es
			// preferible a fallar delante de una alerta vigente.
		}
	}

	function dismiss(banner) {
		remember(banner);
		document.documentElement.classList.add('emergency-banner-dismissed');
		// El botón desaparece con la franja: sin esto el foco cae al `body` y
		// quien navega con teclado pierde el punto donde estaba.
		var body = document.body;
		if (body && !body.hasAttribute('tabindex')) {
			body.setAttribute('tabindex', '-1');
		}
		if (body) {
			body.focus({ preventScroll: true });
		}
	}

	function init() {
		var banner = document.querySelector('[data-emergency-banner]');
		if (!banner) {
			return;
		}
		var button = banner.querySelector('[data-emergency-banner-close]');
		if (!button) {
			return;
		}
		button.addEventListener('click', function () {
			dismiss(banner);
		});
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', init);
	} else {
		init();
	}
})();
