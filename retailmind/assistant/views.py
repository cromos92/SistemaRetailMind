"""
RetailMind Assistant - Views
============================
APIs REST para el asistente conversacional.
"""

import json
import time
import logging
from datetime import datetime

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from .agent import AssistantAgent, AssistantAgentFactory
from .models import (
    ConversacionAsistente, 
    MensajeAsistente, 
    FeedbackAsistente,
    EstadisticasAsistente
)

logger = logging.getLogger(__name__)


# ==================== VISTA PRINCIPAL DEL CHAT ====================

@login_required
def chat_view(request):
    """
    Renderiza la página del chat del asistente.
    """
    # Obtener o crear sesión
    session_id = request.session.get('assistant_session_id')
    if not session_id:
        session_id = f"session_{request.user.id}_{timezone.localtime().strftime('%Y%m%d%H%M%S')}"
        request.session['assistant_session_id'] = session_id
    
    context = {
        'session_id': session_id,
        'user_name': request.user.get_full_name() or request.user.username,
    }
    
    return render(request, 'assistant/chat.html', context)


# ==================== API DE CHAT ====================

@login_required
@require_http_methods(["POST"])
def api_chat(request):
    """
    API para enviar mensajes al asistente y recibir respuestas.
    
    POST /api/assistant/chat/
    
    Body:
    {
        "message": "Tu pregunta aquí",
        "session_id": "opcional_session_id"
    }
    
    Response:
    {
        "success": true,
        "response": "Respuesta del asistente",
        "session_id": "session_id"
    }
    """
    try:
        # Parsear body
        data = json.loads(request.body.decode('utf-8'))
        user_message = data.get('message', '').strip()
        session_id = data.get('session_id') or request.session.get('assistant_session_id')
        
        if not user_message:
            return JsonResponse({
                'success': False,
                'error': 'El mensaje no puede estar vacío'
            }, status=400)
        
        # Limitar longitud del mensaje
        if len(user_message) > 2000:
            return JsonResponse({
                'success': False,
                'error': 'El mensaje es demasiado largo (máximo 2000 caracteres)'
            }, status=400)
        
        # Obtener o crear agente
        agent = AssistantAgentFactory.get_or_create(request.user, session_id)
        
        # Registrar tiempo de inicio
        start_time = time.time()
        
        # Obtener o crear conversación en DB
        conversacion, created = ConversacionAsistente.objects.get_or_create(
            usuario=request.user,
            session_id=session_id,
            defaults={
                'ip_address': get_client_ip(request),
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:500]
            }
        )
        
        # Guardar mensaje del usuario
        mensaje_usuario = MensajeAsistente.objects.create(
            conversacion=conversacion,
            rol='user',
            contenido=user_message
        )
        
        # Procesar mensaje con el agente
        result = agent.chat(user_message)
        
        # Calcular tiempo de respuesta
        tiempo_respuesta = int((time.time() - start_time) * 1000)
        
        # Extraer herramientas usadas del resultado
        tools_usadas = result.get('tools_used', [])
        
        # Guardar respuesta del asistente
        mensaje_asistente = MensajeAsistente.objects.create(
            conversacion=conversacion,
            rol='assistant',
            contenido=result.get('response', ''),
            tiempo_respuesta_ms=tiempo_respuesta,
            tools_usadas=tools_usadas if tools_usadas else None
        )
        
        # Actualizar contadores de conversación
        conversacion.total_mensajes += 2
        conversacion.fecha_ultimo_mensaje = timezone.now()
        conversacion.save(update_fields=['total_mensajes', 'fecha_ultimo_mensaje'])
        
        # Guardar session_id en la sesión de Django
        request.session['assistant_session_id'] = session_id
        
        return JsonResponse({
            'success': True,
            'response': result.get('response', ''),
            'session_id': session_id,
            'message_id': mensaje_asistente.id,
            'tiempo_respuesta_ms': tiempo_respuesta,
            'tools_used': tools_usadas  # Incluir herramientas usadas para el frontend
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'JSON inválido en el body'
        }, status=400)
    except Exception as e:
        logger.error(f"Error en api_chat: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'Error interno del servidor'
        }, status=500)


# ==================== API DE FEEDBACK ====================

@login_required
@require_http_methods(["POST"])
def api_feedback(request):
    """
    API para enviar feedback sobre las respuestas del asistente.
    
    POST /api/assistant/feedback/
    
    Body:
    {
        "message_id": 123,          // ID del mensaje evaluado (opcional)
        "session_id": "session_id", // ID de la sesión (opcional)
        "rating": 4,                // 1-5
        "tipo_feedback": "UTIL",    // Tipo de feedback
        "comentario": "opcional",   // Comentario adicional
        "pregunta": "...",          // Pregunta original (opcional)
        "respuesta": "..."          // Respuesta evaluada (opcional)
    }
    
    Response:
    {
        "success": true,
        "feedback_id": 456
    }
    """
    try:
        data = json.loads(request.body.decode('utf-8'))
        
        # Validar rating
        rating = data.get('rating')
        if not rating or rating not in [1, 2, 3, 4, 5]:
            return JsonResponse({
                'success': False,
                'error': 'Rating debe ser un número del 1 al 5'
            }, status=400)
        
        # Obtener mensaje si se proporcionó
        mensaje = None
        message_id = data.get('message_id')
        if message_id:
            try:
                mensaje = MensajeAsistente.objects.get(
                    id=message_id,
                    conversacion__usuario=request.user
                )
            except MensajeAsistente.DoesNotExist:
                pass
        
        # Obtener conversación si se proporcionó session_id
        conversacion = None
        session_id = data.get('session_id')
        if session_id:
            conversacion = ConversacionAsistente.objects.filter(
                session_id=session_id,
                usuario=request.user
            ).first()
        
        # Crear feedback
        feedback = FeedbackAsistente.objects.create(
            usuario=request.user,
            mensaje=mensaje,
            conversacion=conversacion,
            rating=rating,
            tipo_feedback=data.get('tipo_feedback', 'UTIL'),
            comentario=data.get('comentario', ''),
            pregunta_usuario=data.get('pregunta', ''),
            respuesta_asistente=data.get('respuesta', '')
        )
        
        logger.info(f"Feedback recibido: rating={rating} de usuario {request.user.username}")
        
        return JsonResponse({
            'success': True,
            'feedback_id': feedback.id,
            'message': '¡Gracias por tu feedback!'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'JSON inválido en el body'
        }, status=400)
    except Exception as e:
        logger.error(f"Error en api_feedback: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'Error interno del servidor'
        }, status=500)


# ==================== API DE HISTORIAL ====================

@login_required
@require_http_methods(["GET"])
def api_history(request):
    """
    API para obtener el historial de conversación actual.
    
    GET /api/assistant/history/?session_id=xxx
    
    Response:
    {
        "success": true,
        "session_id": "session_id",
        "messages": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."}
        ]
    }
    """
    try:
        session_id = request.GET.get('session_id') or request.session.get('assistant_session_id')
        
        if not session_id:
            return JsonResponse({
                'success': True,
                'session_id': None,
                'messages': []
            })
        
        # Obtener conversación
        conversacion = ConversacionAsistente.objects.filter(
            session_id=session_id,
            usuario=request.user
        ).first()
        
        if not conversacion:
            return JsonResponse({
                'success': True,
                'session_id': session_id,
                'messages': []
            })
        
        # Obtener mensajes
        mensajes = conversacion.mensajes.filter(
            rol__in=['user', 'assistant']
        ).order_by('timestamp')
        
        messages = [
            {
                'id': m.id,
                'role': m.rol,
                'content': m.contenido,
                'timestamp': m.timestamp.isoformat()
            }
            for m in mensajes
        ]
        
        return JsonResponse({
            'success': True,
            'session_id': session_id,
            'total_messages': len(messages),
            'messages': messages
        })
        
    except Exception as e:
        logger.error(f"Error en api_history: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'Error interno del servidor'
        }, status=500)


# ==================== API PARA NUEVA CONVERSACIÓN ====================

@login_required
@require_http_methods(["POST"])
def api_new_conversation(request):
    """
    API para iniciar una nueva conversación (limpiar historial).
    
    POST /api/assistant/new/
    
    Response:
    {
        "success": true,
        "session_id": "nuevo_session_id"
    }
    """
    try:
        # Generar nuevo session_id
        new_session_id = f"session_{request.user.id}_{timezone.localtime().strftime('%Y%m%d%H%M%S')}"
        
        # Limpiar sesión anterior del cache
        old_session_id = request.session.get('assistant_session_id')
        if old_session_id:
            AssistantAgentFactory.clear_session(request.user, old_session_id)
            
            # Marcar conversación anterior como inactiva
            ConversacionAsistente.objects.filter(
                session_id=old_session_id,
                usuario=request.user
            ).update(activa=False)
        
        # Guardar nuevo session_id
        request.session['assistant_session_id'] = new_session_id
        
        return JsonResponse({
            'success': True,
            'session_id': new_session_id,
            'message': 'Nueva conversación iniciada'
        })
        
    except Exception as e:
        logger.error(f"Error en api_new_conversation: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'Error interno del servidor'
        }, status=500)


# ==================== API DE ESTADÍSTICAS ====================

@login_required
@require_http_methods(["GET"])
def api_stats(request):
    """
    API para obtener estadísticas del asistente (solo admins).
    
    GET /api/assistant/stats/
    """
    if not request.user.is_superuser and getattr(request.user, 'rol', '') != 'administrador':
        return JsonResponse({
            'success': False,
            'error': 'No tienes permisos para ver estadísticas'
        }, status=403)
    
    try:
        # Actualizar estadísticas del día
        stats_hoy = EstadisticasAsistente.actualizar_estadisticas_hoy()
        
        # Estadísticas de los últimos 7 días
        from datetime import timedelta
        hace_7_dias = timezone.localdate() - timedelta(days=7)
        
        stats_semana = EstadisticasAsistente.objects.filter(
            fecha__gte=hace_7_dias
        ).order_by('fecha')
        
        return JsonResponse({
            'success': True,
            'hoy': {
                'conversaciones': stats_hoy.total_conversaciones,
                'mensajes': stats_hoy.total_mensajes,
                'usuarios_unicos': stats_hoy.total_usuarios_unicos,
                'tiempo_respuesta_promedio_ms': stats_hoy.tiempo_respuesta_promedio_ms,
                'rating_promedio': float(stats_hoy.rating_promedio) if stats_hoy.rating_promedio else None,
                'feedback_positivo': stats_hoy.feedback_positivo,
                'feedback_negativo': stats_hoy.feedback_negativo,
            },
            'semana': [
                {
                    'fecha': s.fecha.isoformat(),
                    'conversaciones': s.total_conversaciones,
                    'mensajes': s.total_mensajes,
                    'rating_promedio': float(s.rating_promedio) if s.rating_promedio else None,
                }
                for s in stats_semana
            ]
        })
        
    except Exception as e:
        logger.error(f"Error en api_stats: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'Error interno del servidor'
        }, status=500)


# ==================== UTILIDADES ====================

def get_client_ip(request):
    """Obtiene la IP del cliente"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip
