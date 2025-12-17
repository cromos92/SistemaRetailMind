"""
RetailMind Assistant Agent
==========================
Clase principal del agente conversacional con Claude y Langfuse.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from django.conf import settings

# Anthropic SDK
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    anthropic = None

# Langfuse para tracing
try:
    from langfuse.decorators import observe, langfuse_context
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    # Crear decorador dummy si no está disponible
    def observe(*args, **kwargs):
        def decorator(func):
            return func
        return decorator
    langfuse_context = None

from .tools import AssistantTools
from .prompts import SYSTEM_PROMPT, NO_CONTEXT_PROMPT, TOOLS_CONTEXT

logger = logging.getLogger(__name__)


class AssistantAgent:
    """
    Agente conversacional inteligente para RetailMind.
    
    Utiliza Claude de Anthropic como modelo de lenguaje y 
    Langfuse para observabilidad y tracing.
    """
    
    MODEL = "claude-sonnet-4-5-20250929"
    MAX_TOKENS = 4096
    MAX_TOOL_CALLS = 10  # Máximo de llamadas a tools por turno
    
    def __init__(self, user, session_id: str = None):
        """
        Inicializa el agente con el usuario autenticado.
        
        Args:
            user: Usuario de Django autenticado
            session_id: ID de sesión para mantener contexto
        """
        self.user = user
        self.session_id = session_id or f"session_{user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Inicializar tools
        self.tools = AssistantTools(user)
        self.tools_definitions = AssistantTools.get_tools_definitions()
        
        # Inicializar cliente de Anthropic
        self.client = None
        if ANTHROPIC_AVAILABLE:
            api_key = getattr(settings, 'ANTHROPIC_API_KEY', None)
            if api_key:
                self.client = anthropic.Anthropic(api_key=api_key)
        
        # Historial de conversación
        self.conversation_history: List[Dict[str, Any]] = []
        
        # Determinar prompt según contexto del usuario
        if self.tools.empresa and self.tools.sucursal:
            self.system_prompt = SYSTEM_PROMPT + "\n\n" + TOOLS_CONTEXT
        else:
            self.system_prompt = NO_CONTEXT_PROMPT
    
    def _get_langfuse_metadata(self) -> Dict[str, Any]:
        """Genera metadata para Langfuse"""
        return {
            "user_id": str(self.user.id),
            "session_id": self.session_id,
            "empresa": self.tools.empresa.nombre if self.tools.empresa else None,
            "sucursal": self.tools.sucursal.alias if self.tools.sucursal else None,
            "rol": getattr(self.user, 'rol', 'unknown'),
        }
    
    def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        """
        Ejecuta una herramienta y retorna el resultado.
        
        Args:
            tool_name: Nombre de la herramienta
            tool_input: Parámetros de entrada
        
        Returns:
            Resultado de la herramienta
        """
        try:
            # Obtener método de tools
            method = getattr(self.tools, tool_name, None)
            if not method:
                return {"error": f"Herramienta '{tool_name}' no encontrada"}
            
            # Ejecutar con parámetros
            result = method(**tool_input)
            
            logger.info(f"Tool {tool_name} ejecutada exitosamente")
            return result
            
        except Exception as e:
            logger.error(f"Error ejecutando tool {tool_name}: {str(e)}")
            return {"error": f"Error ejecutando {tool_name}: {str(e)}"}
    
    @observe(name="assistant_chat")
    def chat(self, user_message: str) -> Dict[str, Any]:
        """
        Procesa un mensaje del usuario y genera una respuesta.
        
        Args:
            user_message: Mensaje del usuario
        
        Returns:
            Dict con respuesta, historial y metadata
        """
        if not self.client:
            return {
                "response": "⚠️ El servicio de asistente no está configurado. Contacta al administrador.",
                "error": True
            }
        
        # Agregar metadata a Langfuse si está disponible
        if LANGFUSE_AVAILABLE and langfuse_context:
            try:
                langfuse_context.update_current_observation(
                    metadata=self._get_langfuse_metadata()
                )
            except:
                pass
        
        # Agregar mensaje del usuario al historial
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })
        
        try:
            # Llamar a Claude
            response = self._call_claude()
            
            # Procesar respuesta (ahora devuelve tupla con herramientas usadas)
            assistant_message, tools_used = self._process_response(response)
            
            return {
                "response": assistant_message,
                "session_id": self.session_id,
                "tools_used": tools_used,
                "error": False
            }
            
        except anthropic.APIError as e:
            logger.error(f"Error de API Anthropic: {str(e)}")
            return {
                "response": "😔 Hubo un error al procesar tu consulta. Por favor, intenta de nuevo.",
                "error": True,
                "error_detail": str(e)
            }
        except Exception as e:
            logger.error(f"Error inesperado: {str(e)}")
            return {
                "response": "😔 Ocurrió un error inesperado. Por favor, intenta de nuevo.",
                "error": True,
                "error_detail": str(e)
            }
    
    @observe(name="claude_call")
    def _call_claude(self) -> Any:
        """Realiza la llamada a Claude"""
        return self.client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            system=self.system_prompt,
            tools=self.tools_definitions,
            messages=self.conversation_history
        )
    
    def _process_response(self, response) -> tuple:
        """
        Procesa la respuesta de Claude, ejecutando tools si es necesario.
        
        Args:
            response: Respuesta de la API de Claude
        
        Returns:
            Tupla (mensaje_texto_final, lista_herramientas_usadas)
        """
        tool_calls_count = 0
        tools_used = []  # Lista para rastrear herramientas usadas
        
        while response.stop_reason == "tool_use" and tool_calls_count < self.MAX_TOOL_CALLS:
            tool_calls_count += 1
            
            # Extraer llamadas a tools
            tool_uses = [block for block in response.content if block.type == "tool_use"]
            
            # Agregar respuesta del asistente al historial
            self.conversation_history.append({
                "role": "assistant",
                "content": response.content
            })
            
            # Ejecutar cada tool y preparar resultados
            tool_results = []
            for tool_use in tool_uses:
                logger.info(f"Ejecutando tool: {tool_use.name} con input: {tool_use.input}")
                
                # Rastrear herramienta usada
                tools_used.append(tool_use.name)
                
                result = self._execute_tool(tool_use.name, tool_use.input)
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str)
                })
            
            # Agregar resultados de tools al historial
            self.conversation_history.append({
                "role": "user",
                "content": tool_results
            })
            
            # Llamar a Claude de nuevo con los resultados
            response = self._call_claude()
        
        # Extraer texto de la respuesta final
        text_blocks = [block.text for block in response.content if hasattr(block, 'text')]
        final_message = "\n".join(text_blocks)
        
        # Agregar respuesta final al historial
        self.conversation_history.append({
            "role": "assistant",
            "content": final_message
        })
        
        return final_message, tools_used
    
    def get_history(self) -> List[Dict[str, str]]:
        """
        Retorna el historial de conversación simplificado.
        
        Returns:
            Lista de mensajes con role y content
        """
        simplified_history = []
        
        for msg in self.conversation_history:
            if msg["role"] == "user":
                # Si es un mensaje de usuario normal
                if isinstance(msg["content"], str):
                    simplified_history.append({
                        "role": "user",
                        "content": msg["content"]
                    })
            elif msg["role"] == "assistant":
                # Si es respuesta del asistente
                if isinstance(msg["content"], str):
                    simplified_history.append({
                        "role": "assistant",
                        "content": msg["content"]
                    })
                elif isinstance(msg["content"], list):
                    # Extraer texto de bloques
                    text = ""
                    for block in msg["content"]:
                        if hasattr(block, 'text'):
                            text += block.text
                    if text:
                        simplified_history.append({
                            "role": "assistant",
                            "content": text
                        })
        
        return simplified_history
    
    def clear_history(self):
        """Limpia el historial de conversación"""
        self.conversation_history = []
    
    def load_history(self, history: List[Dict[str, str]]):
        """
        Carga un historial de conversación previo.
        
        Args:
            history: Lista de mensajes con role y content
        """
        self.conversation_history = []
        for msg in history:
            self.conversation_history.append({
                "role": msg["role"],
                "content": msg["content"]
            })


class AssistantAgentFactory:
    """
    Factory para crear instancias del agente con caché de sesión.
    """
    
    _instances: Dict[str, AssistantAgent] = {}
    
    @classmethod
    def get_or_create(cls, user, session_id: str = None) -> AssistantAgent:
        """
        Obtiene o crea una instancia del agente para el usuario/sesión.
        
        Args:
            user: Usuario de Django
            session_id: ID de sesión opcional
        
        Returns:
            Instancia de AssistantAgent
        """
        if not session_id:
            session_id = f"user_{user.id}"
        
        cache_key = f"{user.id}_{session_id}"
        
        if cache_key not in cls._instances:
            cls._instances[cache_key] = AssistantAgent(user, session_id)
        
        return cls._instances[cache_key]
    
    @classmethod
    def clear_session(cls, user, session_id: str = None):
        """
        Limpia la sesión de un usuario.
        
        Args:
            user: Usuario de Django
            session_id: ID de sesión opcional
        """
        if not session_id:
            session_id = f"user_{user.id}"
        
        cache_key = f"{user.id}_{session_id}"
        
        if cache_key in cls._instances:
            del cls._instances[cache_key]
    
    @classmethod
    def clear_all(cls):
        """Limpia todas las sesiones"""
        cls._instances.clear()
