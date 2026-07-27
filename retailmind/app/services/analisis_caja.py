"""
Servicio de detección de patrones sospechosos en arqueos de caja.
Solo visible para supervisores (administrador/administración).
"""
from datetime import timedelta, date
from django.db.models import (
    Count, Sum, Avg, Q, F, Case, When, Value,
    IntegerField, DateTimeField, ExpressionWrapper,
)
from django.db.models.functions import Mod
from django.utils import timezone


class AnalisisFraudeCaja:
    """Analiza patrones sospechosos en arqueos de un cajero o sucursal."""

    def analizar_cajero(self, usuario_id, sucursal_id=None, meses=3):
        from app.models.caja import ArqueoCaja

        fecha_desde = timezone.localdate() - timedelta(days=meses * 30)
        qs = ArqueoCaja.objects.filter(
            usuario_responsable_id=usuario_id,
            fecha_arqueo__gte=fecha_desde,
            estado__in=['CERRADO', 'CON_DIFERENCIAS', 'DEPOSITO_DECLARADO',
                        'DEPOSITO_CONFIRMADO', 'REVISADO'],
        )
        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)

        total = qs.count()
        if total == 0:
            return {
                'total_arqueos': 0,
                'nivel_riesgo': 'SIN_DATOS',
                'indicadores': {},
            }

        # Los 6 indicadores se calculan UNA sola vez y se reutilizan para el
        # nivel de riesgo. Antes `_evaluar_riesgo(qs, total)` los recalculaba
        # todos desde cero: el análisis costaba exactamente el doble de
        # consultas (medido: 70 queries / 16,2 s para un cajero con 107
        # arqueos contra producción).
        indicadores = {
            'porcentaje_exactos': self._porcentaje_exactos(qs, total),
            'faltante_acumulado': self._faltante_acumulado(qs),
            'anomalias_timing': self._anomalias_timing(qs, total),
            'uso_express_pct': self._porcentaje_express(qs, total),
            'numeros_redondos_pct': self._numeros_redondos(qs, total),
            'deposito_vs_teorico': self._deposito_vs_teorico(qs),
        }

        return {
            'total_arqueos': total,
            'indicadores': indicadores,
            'nivel_riesgo': self._evaluar_riesgo(indicadores),
        }

    def analizar_sucursal(self, sucursal_id, meses=3):
        from app.models.caja import ArqueoCaja
        from django.contrib.auth import get_user_model
        Usuario = get_user_model()

        fecha_desde = timezone.localdate() - timedelta(days=meses * 30)

        # Obtener cajeros que han hecho arqueos en esta sucursal
        cajeros_ids = ArqueoCaja.objects.filter(
            sucursal_id=sucursal_id,
            fecha_arqueo__gte=fecha_desde,
        ).values_list('usuario_responsable_id', flat=True).distinct()

        resultados = []
        for uid in cajeros_ids:
            usuario = Usuario.objects.filter(id=uid).first()
            if not usuario:
                continue
            analisis = self.analizar_cajero(uid, sucursal_id, meses)
            resultados.append({
                'usuario_id': uid,
                'usuario_nombre': usuario.get_full_name() or usuario.username,
                'rol': getattr(usuario, 'rol', 'sin_rol'),
                **analisis,
            })

        # Ordenar por nivel de riesgo (ALTO primero)
        orden_riesgo = {'ALTO': 0, 'MEDIO': 1, 'BAJO': 2, 'SIN_DATOS': 3}
        resultados.sort(key=lambda x: orden_riesgo.get(x['nivel_riesgo'], 3))

        return resultados

    # === INDICADORES INDIVIDUALES ===

    def _porcentaje_exactos(self, qs, total):
        """Arqueos con diferencia=0. >80% es sospechoso (posible copia del teórico)."""
        exactos = qs.filter(diferencia_efectivo=0).count()
        pct = round(exactos / total * 100, 1) if total > 0 else 0
        return {
            'valor': pct,
            'exactos': exactos,
            'total': total,
            'alerta': pct > 80,
            'descripcion': f'{exactos} de {total} arqueos con diferencia exacta $0',
        }

    def _faltante_acumulado(self, qs):
        """Suma de faltantes (diferencias negativas). Faltantes chicos que suman."""
        faltantes = qs.filter(diferencia_efectivo__lt=0)
        total_faltante = faltantes.aggregate(t=Sum('diferencia_efectivo'))['t'] or 0
        count = faltantes.count()
        promedio = round(total_faltante / count) if count > 0 else 0
        return {
            'valor': total_faltante,
            'cantidad_faltantes': count,
            'promedio_faltante': promedio,
            'alerta': total_faltante < -10000,  # Acumulado > $10,000
            'descripcion': f'{count} faltantes que suman ${abs(total_faltante):,}',
        }

    def _anomalias_timing(self, qs, total):
        """Conteos guardados en menos de 2 minutos desde la creación del arqueo."""
        # Se resuelve en la base (timestamp < fecha_creacion + 120 s) en vez de
        # traer los arqueos y restar en Python: mismo criterio, incluidos los
        # deltas negativos, pero con una sola consulta de conteo.
        rapidos = qs.filter(
            timestamp_conteo_fisico__isnull=False,
            fecha_creacion__isnull=False,
        ).annotate(
            _limite_rapido=ExpressionWrapper(
                F('fecha_creacion') + timedelta(seconds=120),
                output_field=DateTimeField(),
            )
        ).filter(timestamp_conteo_fisico__lt=F('_limite_rapido')).count()
        pct = round(rapidos / total * 100, 1) if total > 0 else 0
        return {
            'valor': pct,
            'rapidos': rapidos,
            'alerta': pct > 30,  # >30% de conteos en <2min
            'descripcion': f'{rapidos} conteos en menos de 2 minutos',
        }

    def _porcentaje_express(self, qs, total):
        """Porcentaje de arqueos en modo express (sin detalle de denominaciones)."""
        express = qs.filter(modo_conteo='EXPRESS').count()
        pct = round(express / total * 100, 1) if total > 0 else 0
        return {
            'valor': pct,
            'express': express,
            'alerta': pct > 60,
            'descripcion': f'{express} de {total} arqueos en modo express',
        }

    def _numeros_redondos(self, qs, total):
        """Conteos físicos que terminan en 000 (sospechoso si es muy frecuente)."""
        # El módulo lo hace el motor (MOD(total_efectivo_fisico, 1000) = 0):
        # antes se traían todos los arqueos con efectivo solo para contarlos.
        redondos = qs.filter(total_efectivo_fisico__gt=0).annotate(
            _resto_mil=Mod(
                F('total_efectivo_fisico'),
                Value(1000, output_field=IntegerField()),
            )
        ).filter(_resto_mil=0).count()
        pct = round(redondos / total * 100, 1) if total > 0 else 0
        return {
            'valor': pct,
            'redondos': redondos,
            'alerta': pct > 50,
            'descripcion': f'{redondos} conteos con montos redondos (terminados en 000)',
        }

    def _deposito_vs_teorico(self, qs):
        """Diferencia acumulada entre lo depositado y lo teórico."""
        # Antes esto recorría TODOS los arqueos leyendo la property
        # `total_depositado_verificado`, que para los arqueos sin caché
        # denormalizada dispara una consulta a DepositoBancario por fila (N+1)
        # y era la causa de que el análisis no terminara. Aquí se resuelve con
        # dos agregaciones, respetando exactamente la misma fórmula.
        # OJO: el teórico NO se toca, se suma tal cual está persistido.
        total_teorico = qs.aggregate(t=Sum('total_efectivo_teorico'))['t'] or 0

        # Rama con caché: réplica literal de la property, o sea
        #   total = cache_total_dep_verificado or 0
        #   if total == 0 and cache_depositos_confirmados and cache_total_depositos:
        #       total = cache_total_depositos
        valor_cacheado = Case(
            When(
                Q(cache_total_dep_verificado__isnull=False)
                & ~Q(cache_total_dep_verificado=0),
                then=F('cache_total_dep_verificado'),
            ),
            When(
                Q(cache_depositos_confirmados__gt=0)
                & Q(cache_total_depositos__gt=0),
                then=F('cache_total_depositos'),
            ),
            default=Value(0),
            output_field=IntegerField(),
        )
        total_depositado = qs.filter(
            cache_depositos_actualizado__isnull=False
        ).aggregate(t=Sum(valor_cacheado))['t'] or 0

        # Arqueos sin caché: una sola consulta agrupada sobre DepositoBancario
        # en lugar de una por arqueo.
        ids_sin_cache = list(
            qs.filter(cache_depositos_actualizado__isnull=True)
            .values_list('id', flat=True)
        )
        if ids_sin_cache:
            from app.models.caja import DepositoBancario
            monto_verificado = Case(
                When(monto_confirmado__gt=0, then=F('monto_confirmado')),
                default=F('monto'),
                output_field=IntegerField(),
            )
            total_depositado += DepositoBancario.objects.filter(
                arqueo_id__in=ids_sin_cache, verificado=True
            ).aggregate(t=Sum(monto_verificado))['t'] or 0

        diferencia = total_depositado - total_teorico
        pct = round(total_depositado / total_teorico * 100, 1) if total_teorico > 0 else 0
        return {
            'valor': diferencia,
            'total_depositado': total_depositado,
            'total_teorico': total_teorico,
            'porcentaje_depositado': pct,
            'alerta': diferencia < -20000,  # Más de $20,000 sin depositar
            'descripcion': f'Depositado ${total_depositado:,} de ${total_teorico:,} teórico ({pct}%)',
        }

    def _evaluar_riesgo(self, indicadores):
        """
        Evalúa el nivel de riesgo global: BAJO, MEDIO, ALTO.

        Recibe el dict de indicadores YA calculado por `analizar_cajero`; antes
        recibía el queryset y recalculaba los seis desde cero, duplicando el
        costo del análisis sin cambiar el resultado.
        """
        alertas = 0
        for ind in (indicadores or {}).values():
            if isinstance(ind, dict) and ind.get('alerta'):
                alertas += 1

        if alertas >= 3:
            return 'ALTO'
        elif alertas >= 1:
            return 'MEDIO'
        return 'BAJO'
