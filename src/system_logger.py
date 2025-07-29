"""
Sistema de Logs Limpo e Cache de Verificações
Reduz logs repetitivos e melhora UX da inicialização
"""

import time
import logging
from enum import Enum
from typing import Dict, Any, Optional
from datetime import datetime


class LogLevel(Enum):
    """Níveis de log do sistema"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


class SystemLogger:
    """
    Gerenciador de logs limpo com cache de verificações
    Evita logs repetitivos e melhora UX
    """
    
    def __init__(self):
        self.verification_cache: Dict[str, Any] = {}
        self.initialization_steps: Dict[str, bool] = {}
        self.start_time = time.time()
        self.verbose_mode = False
        
        # Configurar logging para suprimir logs externos
        self._configure_external_logging()
        
    def _configure_external_logging(self):
        """Configura logging para suprimir logs externos verbosos"""
        # Suprimir logs do httpx
        logging.getLogger("httpx").setLevel(logging.WARNING)
        
        # Suprimir logs do urllib3
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        
        # Suprimir logs do requests
        logging.getLogger("requests").setLevel(logging.WARNING)
        
        # Suprimir logs do supabase
        logging.getLogger("supabase").setLevel(logging.WARNING)
        
        # Suprimir logs do postgrest
        logging.getLogger("postgrest").setLevel(logging.WARNING)
        
    def set_verbose(self, verbose: bool = True):
        """Define se deve mostrar logs detalhados"""
        self.verbose_mode = verbose
    
    def clear_cache(self):
        """
        Limpa o cache de verificações para nova execução
        Útil para reinicializar o sistema
        """
        self.verification_cache.clear()
        self.initialization_steps.clear()
        self.start_time = time.time()
        
    def cache_verification(self, key: str, value: Any, message: str = None):
        """
        Armazena verificação no cache para evitar repetições
        
        Args:
            key: Chave única da verificação
            value: Valor verificado
            message: Mensagem opcional para log
        """
        if key not in self.verification_cache:
            self.verification_cache[key] = {
                'value': value,
                'timestamp': datetime.now(),
                'verified': True
            }
            if message and self.verbose_mode:
                self.log(LogLevel.DEBUG, message)
    
    def get_cached_verification(self, key: str) -> Optional[Any]:
        """
        Recupera verificação do cache
        
        Args:
            key: Chave da verificação
            
        Returns:
            Valor verificado ou None se não existe
        """
        cached = self.verification_cache.get(key)
        return cached['value'] if cached else None
    
    def get_device_id_short(self, device_id: str) -> str:
        """
        Retorna uma versão curta do Device ID para logs
        
        Args:
            device_id: Device ID completo
            
        Returns:
            Versão curta do Device ID (primeiros 8 caracteres)
        """
        if not device_id:
            return "N/A"
        return device_id[:8] + "..." if len(device_id) > 8 else device_id
    
    def is_cached(self, key: str) -> bool:
        """Verifica se uma chave já foi verificada (alias para is_verified)"""
        return key in self.verification_cache
    
    def is_verified(self, key: str) -> bool:
        """Verifica se uma chave já foi verificada"""
        return key in self.verification_cache
    
    def mark_step_complete(self, step: str, success: bool = True):
        """Marca uma etapa de inicialização como completa"""
        self.initialization_steps[step] = success
        
    def log(self, level: LogLevel, message: str, emoji: str = None):
        """
        Log com níveis e formatação consistente
        
        Args:
            level: Nível do log
            message: Mensagem
            emoji: Emoji opcional
        """
        if not self.verbose_mode and level == LogLevel.DEBUG:
            return
            
        # Emojis padrão por nível
        level_emojis = {
            LogLevel.DEBUG: "🔍",
            LogLevel.INFO: "ℹ️",
            LogLevel.WARNING: "⚠️",
            LogLevel.ERROR: "❌",
            LogLevel.SUCCESS: "✅"
        }
        
        display_emoji = emoji or level_emojis.get(level, "")
        print(f"{display_emoji} {message}")


# Instância global do logger
system_logger = SystemLogger()


def log_debug(message: str, emoji: str = None):
    """Shortcut para log de debug"""
    system_logger.log(LogLevel.DEBUG, message, emoji)


def log_info(message: str, emoji: str = None):
    """Shortcut para log de info"""
    system_logger.log(LogLevel.INFO, message, emoji)


def log_warning(message: str, emoji: str = None):
    """Shortcut para log de warning"""
    system_logger.log(LogLevel.WARNING, message, emoji)


def log_error(message: str, emoji: str = None):
    """Shortcut para log de error"""
    system_logger.log(LogLevel.ERROR, message, emoji)


def log_success(message: str, emoji: str = None):
    """Shortcut para log de success"""
    system_logger.log(LogLevel.SUCCESS, message, emoji)