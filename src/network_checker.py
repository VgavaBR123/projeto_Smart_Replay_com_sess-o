# -*- coding: utf-8 -*-
"""
Módulo de Verificação de Conectividade de Rede
Sistema de Câmeras de Segurança

Este módulo verifica a conectividade de rede antes de fazer upload para o Supabase.
Se o sistema estiver offline, os vídeos são mantidos localmente.
"""

import os
import socket
import time
import requests
from urllib.parse import urlparse
from system_logger import log_info, log_success, log_warning, log_error, log_debug

class NetworkConnectivityChecker:
    """
    Classe responsável por verificar a conectividade de rede e disponibilidade do Supabase.
    """
    
    def __init__(self):
        """
        Inicializa o verificador de conectividade.
        """
        self.supabase_url = os.getenv('SUPABASE_URL', '')
        self.timeout_seconds = int(os.getenv('NETWORK_CHECK_TIMEOUT', '10'))
        self.retry_attempts = int(os.getenv('NETWORK_CHECK_RETRIES', '3'))
        self.retry_delay = float(os.getenv('NETWORK_CHECK_RETRY_DELAY', '2.0'))
        
        # URLs de teste para verificação de conectividade geral
        self.test_urls = [
            'https://www.google.com',
            'https://www.cloudflare.com',
            '8.8.8.8'  # DNS do Google
        ]
        
        log_info("🌐 NetworkConnectivityChecker inicializado")
    
    def check_internet_connectivity(self) -> dict:
        """
        Verifica se há conectividade geral com a internet.
        
        Returns:
            dict: Resultado da verificação
        """
        log_info("🔍 Verificando conectividade geral com a internet...")
        
        for attempt in range(self.retry_attempts):
            try:
                # Teste 1: Verificação DNS
                if self._check_dns_resolution():
                    log_debug("✅ Resolução DNS funcionando")
                    
                    # Teste 2: Conectividade HTTP
                    if self._check_http_connectivity():
                        log_success("🌐 Conectividade com a internet confirmada")
                        return {
                            'success': True,
                            'online': True,
                            'message': 'Conectividade com a internet confirmada',
                            'attempt': attempt + 1
                        }
                
                if attempt < self.retry_attempts - 1:
                    log_warning(f"⚠️ Tentativa {attempt + 1} falhou, tentando novamente em {self.retry_delay}s...")
                    time.sleep(self.retry_delay)
                    
            except Exception as e:
                log_error(f"❌ Erro na verificação de conectividade (tentativa {attempt + 1}): {e}")
                if attempt < self.retry_attempts - 1:
                    time.sleep(self.retry_delay)
        
        log_warning("🔌 Sistema detectado como OFFLINE")
        return {
            'success': True,
            'online': False,
            'message': 'Sistema offline - sem conectividade com a internet',
            'attempts': self.retry_attempts
        }
    
    def check_supabase_connectivity(self) -> dict:
        """
        Verifica se o Supabase está acessível e funcionando.
        
        Returns:
            dict: Resultado da verificação do Supabase
        """
        if not self.supabase_url:
            log_error("❌ URL do Supabase não configurada")
            return {
                'success': False,
                'online': False,
                'message': 'URL do Supabase não configurada',
                'error': 'SUPABASE_URL não encontrada'
            }
        
        log_info(f"🔍 Verificando conectividade com Supabase: {self._sanitize_url(self.supabase_url)}")
        
        for attempt in range(self.retry_attempts):
            try:
                # Fazer uma requisição simples para o endpoint de health do Supabase
                health_url = f"{self.supabase_url}/rest/v1/"
                
                response = requests.get(
                    health_url,
                    timeout=self.timeout_seconds,
                    headers={
                        'User-Agent': 'CameraSystem/1.0',
                        'Accept': 'application/json'
                    }
                )
                
                if response.status_code in [200, 401, 403]:  # 401/403 indicam que o serviço está funcionando
                    log_success(f"☁️ Supabase acessível (status: {response.status_code})")
                    return {
                        'success': True,
                        'online': True,
                        'message': f'Supabase acessível (status: {response.status_code})',
                        'status_code': response.status_code,
                        'attempt': attempt + 1
                    }
                else:
                    log_warning(f"⚠️ Supabase retornou status inesperado: {response.status_code}")
                    
            except requests.exceptions.Timeout:
                log_warning(f"⏱️ Timeout na conexão com Supabase (tentativa {attempt + 1})")
            except requests.exceptions.ConnectionError:
                log_warning(f"🔌 Erro de conexão com Supabase (tentativa {attempt + 1})")
            except Exception as e:
                log_error(f"❌ Erro inesperado ao verificar Supabase (tentativa {attempt + 1}): {e}")
            
            if attempt < self.retry_attempts - 1:
                log_info(f"🔄 Tentando novamente em {self.retry_delay}s...")
                time.sleep(self.retry_delay)
        
        log_error("❌ Supabase inacessível após todas as tentativas")
        return {
            'success': True,
            'online': False,
            'message': 'Supabase inacessível após todas as tentativas',
            'attempts': self.retry_attempts
        }
    
    def check_full_connectivity(self) -> dict:
        """
        Verifica conectividade completa: internet + Supabase.
        
        Returns:
            dict: Resultado completo da verificação
        """
        log_info("🚀 Iniciando verificação completa de conectividade...")
        
        # Verificar conectividade geral
        internet_result = self.check_internet_connectivity()
        
        if not internet_result['online']:
            log_warning("🔌 Sistema offline - mantendo vídeos localmente")
            return {
                'success': True,
                'internet_online': False,
                'supabase_online': False,
                'upload_enabled': False,
                'message': 'Sistema offline - vídeos serão mantidos localmente',
                'details': {
                    'internet': internet_result,
                    'supabase': {'skipped': True, 'reason': 'Internet offline'}
                }
            }
        
        # Se internet está OK, verificar Supabase
        supabase_result = self.check_supabase_connectivity()
        
        upload_enabled = internet_result['online'] and supabase_result['online']
        
        if upload_enabled:
            log_success("✅ Sistema ONLINE - upload habilitado")
            message = "Sistema online - upload para Supabase habilitado"
        else:
            log_warning("⚠️ Supabase inacessível - mantendo vídeos localmente")
            message = "Internet OK, mas Supabase inacessível - vídeos mantidos localmente"
        
        return {
            'success': True,
            'internet_online': internet_result['online'],
            'supabase_online': supabase_result['online'],
            'upload_enabled': upload_enabled,
            'message': message,
            'details': {
                'internet': internet_result,
                'supabase': supabase_result
            }
        }
    
    def _check_dns_resolution(self) -> bool:
        """
        Verifica se a resolução DNS está funcionando.
        
        Returns:
            bool: True se DNS está funcionando
        """
        try:
            socket.gethostbyname('www.google.com')
            return True
        except socket.gaierror:
            return False
    
    def _check_http_connectivity(self) -> bool:
        """
        Verifica conectividade HTTP com URLs de teste.
        
        Returns:
            bool: True se pelo menos uma URL responder
        """
        for url in self.test_urls:
            try:
                if url == '8.8.8.8':
                    # Teste de ping para IP
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(self.timeout_seconds)
                    result = sock.connect_ex((url, 53))  # DNS port
                    sock.close()
                    if result == 0:
                        return True
                else:
                    # Teste HTTP
                    response = requests.get(
                        url,
                        timeout=self.timeout_seconds,
                        headers={'User-Agent': 'CameraSystem/1.0'}
                    )
                    if response.status_code == 200:
                        return True
            except:
                continue
        return False
    
    def _sanitize_url(self, url: str) -> str:
        """
        Sanitiza URL para logs (remove informações sensíveis).
        
        Args:
            url (str): URL original
            
        Returns:
            str: URL sanitizada
        """
        try:
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}"
        except:
            return "[URL inválida]"
    
    def get_connectivity_status_summary(self) -> str:
        """
        Retorna um resumo rápido do status de conectividade.
        
        Returns:
            str: Resumo do status
        """
        result = self.check_full_connectivity()
        
        if result['upload_enabled']:
            return "🟢 ONLINE - Upload habilitado"
        elif result['internet_online']:
            return "🟡 PARCIAL - Internet OK, Supabase inacessível"
        else:
            return "🔴 OFFLINE - Sem conectividade"