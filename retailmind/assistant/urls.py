"""
RetailMind Assistant - URLs
===========================
Configuración de URLs para el asistente conversacional.
"""

from django.urls import path
from . import views

app_name = 'assistant'

urlpatterns = [
    # Vista principal del chat
    path('', views.chat_view, name='chat'),
    
    # APIs
    path('api/chat/', views.api_chat, name='api_chat'),
    path('api/feedback/', views.api_feedback, name='api_feedback'),
    path('api/history/', views.api_history, name='api_history'),
    path('api/new/', views.api_new_conversation, name='api_new_conversation'),
    path('api/stats/', views.api_stats, name='api_stats'),
]
