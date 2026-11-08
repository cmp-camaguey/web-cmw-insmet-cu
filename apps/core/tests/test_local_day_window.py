from datetime import date, datetime, timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from apps.core.utils import local_day_window


class LocalDayWindowTests(SimpleTestCase):
    """`local_day_window` es la unica fuente de verdad del dia local.

    Los lookups `__date` resuelven la zona dentro de PostgreSQL con el tzdata de
    la base; esta funcion la resuelve en Python. Ninguna prueba de este archivo
    fija en que fecha caen los cambios de hora ni cuanto dura un dia concreto:
    eso depende del tzdata instalado, y es precisamente lo que no se puede
    asumir. Las pruebas derivan la expectativa del mismo tzdata del entorno.
    """

    @staticmethod
    def _en_hora_local(instante):
        """Vuelve a resolver el instante en la zona activa.

        `astimezone` a la misma zona no recalcula nada: si el `datetime` ya trae
        esa `tzinfo`, devuelve el valor tal cual, con su hora de pared original.
        Para una hora inexistente (la medianoche que se salta el dia que arranca
        el horario de verano) eso deja la hora equivocada. `fromtimestamp` si
        recalcula el desplazamiento que corresponde al instante.
        """
        return datetime.fromtimestamp(instante.timestamp(), timezone.get_current_timezone())

    def test_el_rango_es_semiabierto_y_esta_en_la_zona_activa(self):
        inicio, fin = local_day_window(date(2026, 11, 8))

        self.assertTrue(timezone.is_aware(inicio))
        self.assertTrue(timezone.is_aware(fin))
        self.assertEqual(inicio.tzinfo, timezone.get_current_timezone())
        self.assertEqual(fin.tzinfo, timezone.get_current_timezone())
        self.assertLess(inicio, fin)

    def test_la_ventana_contiene_exactamente_el_dia_local(self):
        """El contrato: `[inicio, fin)` abarca el dia local completo y nada mas.

        Se recorre un anio entero, cambios de hora incluidos, sin fijar de
        antemano en que fecha caen: los dos tzdata en juego (el de Python y el
        de PostgreSQL) no siempre coinciden, y por eso el codigo no puede asumir
        dias de 24 horas. Si alguien reescribe la funcion con
        `timedelta(days=1)` sobre una fecha ingenua, los dias con cambio de hora
        se salen del rango o se tragan una hora del vecino y esto falla.
        """
        un_microsegundo = timedelta(microseconds=1)

        for desplazamiento in range(365):
            dia = date(2026, 1, 1) + timedelta(days=desplazamiento)
            inicio, fin = local_day_window(dia)
            with self.subTest(dia=dia):
                # El primer instante de la ventana ya es del dia local pedido.
                self.assertEqual(self._en_hora_local(inicio).date(), dia)
                # El instante anterior todavia es del dia previo.
                self.assertEqual(
                    self._en_hora_local(inicio - un_microsegundo).date(),
                    dia - timedelta(days=1),
                )
                # Y la ventana termina justo al empezar el dia siguiente.
                self.assertEqual(self._en_hora_local(fin).date(), dia + timedelta(days=1))
                self.assertEqual(self._en_hora_local(fin - un_microsegundo).date(), dia)
