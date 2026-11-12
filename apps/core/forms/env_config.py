"""Formulario de los parámetros editables del entorno.

Los campos se construyen desde `EDITABLE_FIELDS` para que el catálogo viva en un
solo lugar: agregar un parámetro administrable es agregar una entrada en
`apps/core/env_config.py`, no tocar el formulario, la plantilla y el test por
separado.

La validación no es cosmética: `config/settings/base.py` y `production.py`
levantan `ImproperlyConfigured` al arrancar con ciertas combinaciones. Un panel
que permitiera guardarlas dejaría el sitio sin levantar y sin forma de
corregirlo desde la web, porque la web es lo que no arranca.
"""

from __future__ import annotations

import urllib.parse

from django import forms

from apps.core.env_config import EDITABLE_FIELDS, effective_value, same_value

# Puerto que exige TLS implícito. Es una constante y no un literal suelto porque
# la misma regla aparece en `production.py`.
SMTPS_PORT = 465


class EnvironmentConfigForm(forms.Form):
    """Solo la lista blanca. Lo que no está acá no se puede escribir."""

    def __init__(self, *args, writable=True, **kwargs):
        super().__init__(*args, **kwargs)
        for spec in EDITABLE_FIELDS:
            field = self._build_field(spec)
            if not writable:
                field.disabled = True
            self.fields[spec.key] = field

    @staticmethod
    def _build_field(spec) -> forms.Field:
        common = {'label': spec.label, 'help_text': spec.help_text, 'required': spec.required}
        # El placeholder muestra la forma esperada del valor, no el valor
        # guardado: nunca se precarga una credencial ni una ruta del servidor.
        text_attrs = {'class': 'form-control'}
        if spec.placeholder:
            text_attrs['placeholder'] = spec.placeholder
        if spec.kind == 'bool':
            return forms.BooleanField(
                required=False,
                label=spec.label,
                help_text=spec.help_text,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            )
        if spec.kind == 'password':
            # `render_value=False` es la garantía de que el valor guardado no
            # vuelve al navegador ni siquiera como `value=""` del input.
            return forms.CharField(
                required=False,
                label=spec.label,
                help_text=spec.help_text,
                widget=forms.PasswordInput(
                    attrs={**text_attrs, 'autocomplete': 'new-password'},
                    render_value=False,
                ),
            )
        if spec.kind == 'int':
            return forms.IntegerField(
                min_value=spec.min_value,
                max_value=spec.max_value,
                widget=forms.TextInput(attrs={**text_attrs, 'inputmode': 'numeric'}),
                **common,
            )
        if spec.kind == 'url':
            return forms.URLField(
                assume_scheme='',
                widget=forms.TextInput(attrs=text_attrs),
                **common,
            )
        return forms.CharField(
            max_length=255,
            widget=forms.TextInput(attrs=text_attrs),
            **common,
        )

    def clean(self):
        cleaned = super().clean()
        # `_quote_value` rechaza saltos de línea al escribir. Se validan acá
        # para que el operador vea un error de formulario y no un 500.
        for spec in EDITABLE_FIELDS:
            value = cleaned.get(spec.key)
            if isinstance(value, str) and ('\n' in value or '\r' in value):
                self.add_error(spec.key, 'El valor no puede contener saltos de línea.')

        self._clean_email(cleaned)
        self._clean_ldap(cleaned)
        return cleaned

    def _clean_email(self, cleaned):
        host = (cleaned.get('EMAIL_HOST') or '').strip()
        port = cleaned.get('EMAIL_PORT')
        tls = cleaned.get('EMAIL_USE_TLS')
        ssl = cleaned.get('EMAIL_USE_SSL')

        if host and port is None:
            self.add_error('EMAIL_PORT', 'Indique el puerto del servidor SMTP.')
        if tls and ssl:
            self.add_error(
                'EMAIL_USE_SSL',
                'STARTTLS y TLS implícito no pueden estar activos a la vez: el sitio '
                'no arrancaría. Use el 587 con STARTTLS o el 465 con TLS implícito.',
            )
        elif port == SMTPS_PORT and not ssl:
            self.add_error(
                'EMAIL_USE_SSL',
                'El puerto 465 espera TLS implícito desde el primer byte: active TLS '
                'implícito y desactive STARTTLS.',
            )

    def _clean_ldap(self, cleaned):
        # Con LDAP apagado el backend no se agrega, así que una configuración a
        # medias no rompe nada y no hay razón para bloquear el guardado.
        if not cleaned.get('LDAP_ENABLED'):
            return
        server = (cleaned.get('LDAP_SERVER_URI') or '').strip()
        search_base = (cleaned.get('LDAP_USER_SEARCH_BASE') or '').strip()
        if not server:
            self.add_error(
                'LDAP_SERVER_URI',
                'Para activar LDAP indique el servidor del directorio.',
            )
        elif not search_base:
            self.add_error(
                'LDAP_USER_SEARCH_BASE',
                'Sin base de búsqueda no se puede consultar el directorio y todo '
                'inicio de sesión LDAP fallaría.',
            )

    def clean_SITE_URL(self):  # noqa: N802 - Django exige el nombre exacto del campo
        value = (self.cleaned_data.get('SITE_URL') or '').strip()
        parts = urllib.parse.urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise forms.ValidationError(
                'La URL debe empezar por http:// o https:// e incluir un host.'
            )
        return value.rstrip('/') + '/'

    def to_updates(self, current=None, settings_obj=None) -> dict[str, str]:
        """Solo las claves que cambian respecto del archivo.

        Escribir todas las claves en cada guardado ensuciaría el archivo con
        valores que nadie tocó. Si la clave no está en el archivo y el valor
        coincide con el efectivo, tampoco se agrega: no hay nada que corregir.

        Un campo de contraseña vacío significa "no cambiar": no se puede
        distinguir de "quiero dejarla en blanco", y borrar una credencial por
        accidente es peor que exigir editarla a mano.
        """
        current = current or {}
        updates: dict[str, str] = {}

        for spec in EDITABLE_FIELDS:
            value = self.cleaned_data.get(spec.key)
            if spec.secret and not value:
                continue
            if spec.kind == 'bool':
                new_value = 'True' if value else 'False'
            elif spec.kind == 'int':
                new_value = '' if value is None else str(value)
            else:
                new_value = (value or '').strip()

            old_value = current.get(spec.key)
            if old_value is None:
                effective = effective_value(spec.key, settings_obj) if settings_obj else None
                if settings_obj is not None and same_value(spec, new_value, effective):
                    continue
            elif same_value(spec, old_value, new_value):
                continue

            updates[spec.key] = new_value

        return updates
