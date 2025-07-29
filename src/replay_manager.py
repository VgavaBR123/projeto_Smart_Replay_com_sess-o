#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerenciador de Replays
Gerencia a inserção de registros na tabela 'replays' do Supabase após uploads bem-sucedidos de vídeos.
Integra com o sistema existente de câmeras de segurança.
"""

import os
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Union
from dotenv import load_dotenv

# Importações do sistema existente
from system_logger import log_info, log_success, log_warning, log_error, log_debug, system_logger


class ReplayManager:
    """
    Gerenciador de registros de replay na tabela Supabase.
    
    Responsável por:
    - Inserir registros na tabela 'replays' após uploads bem-sucedidos
    - Gerar URLs assinadas para vídeos
    - Validar dados antes da inserção
    - Sistema de retry com backoff exponencial
    - Logs compatíveis com system_logger
    """
    
    def __init__(self, supabase_manager=None):
        """
        Inicializa o ReplayManager.
        
        Args:
            supabase_manager (SupabaseManager): Instância do SupabaseManager para reutilizar conexão
        """
        self.supabase_manager = supabase_manager
        self.supabase = None
        self.bucket_name = "videos-replay"
        
        # Carregar configurações
        self._carregar_configuracoes()
        
        # Configurações de retry
        self.max_retries = int(os.getenv('REPLAY_MAX_RETRIES', '3'))
        self.retry_delay_base = float(os.getenv('REPLAY_RETRY_DELAY_BASE', '1.0'))
        self.retry_backoff_multiplier = float(os.getenv('REPLAY_RETRY_BACKOFF_MULTIPLIER', '2.0'))
        
        # Inicializar conexão
        self._inicializar_conexao()
        
        log_info("ReplayManager inicializado com sucesso")
    
    def _carregar_configuracoes(self):
        """Carrega as configurações do arquivo config.env"""
        try:
            # Tenta carregar config.env (na raiz do projeto)
            env_file = Path(__file__).parent.parent / "config.env"
            if env_file.exists():
                load_dotenv(env_file)
                log_debug(f"Configurações carregadas de: {env_file}")
            else:
                log_warning("Arquivo config.env não encontrado")
                
        except Exception as e:
            log_error(f"Erro ao carregar configurações: {e}")
    
    def _inicializar_conexao(self):
        """Inicializa a conexão com o Supabase"""
        try:
            if self.supabase_manager and hasattr(self.supabase_manager, 'supabase'):
                # Reutiliza conexão existente
                self.supabase = self.supabase_manager.supabase
                log_debug("Reutilizando conexão Supabase existente")
            else:
                log_warning("SupabaseManager não fornecido ou sem conexão")
                
        except Exception as e:
            log_error(f"Erro ao inicializar conexão Supabase: {e}")
    
    def _validar_dados_replay(self, camera_id: str, video_url: str, timestamp_video: datetime, bucket_path: str) -> Dict[str, Any]:
        """
        Valida os dados antes da inserção na tabela replays.
        
        Args:
            camera_id (str): UUID da câmera
            video_url (str): URL do vídeo original
            timestamp_video (datetime): Momento da gravação (deve estar em UTC)
            bucket_path (str): Caminho no bucket
            
        Returns:
            dict: Resultado da validação
        """
        try:
            # Validar camera_id como UUID
            try:
                uuid.UUID(camera_id)
            except ValueError:
                return {'success': False, 'error': f'camera_id inválido: {camera_id}'}
            
            # Validar video_url
            if not video_url or not isinstance(video_url, str):
                return {'success': False, 'error': 'video_url é obrigatório e deve ser string'}
            
            # Validar timestamp_video
            if not isinstance(timestamp_video, datetime):
                return {'success': False, 'error': 'timestamp_video deve ser datetime'}
            
            # Validar bucket_path
            if not bucket_path or not isinstance(bucket_path, str):
                return {'success': False, 'error': 'bucket_path é obrigatório e deve ser string'}
            
            # Verificar se a câmera existe na tabela cameras
            if self.supabase:
                try:
                    camera_response = self.supabase.table('cameras').select('id, nome, ordem').eq('id', camera_id).execute()
                    if not camera_response.data:
                        log_error(f"Câmera não encontrada na tabela: {camera_id}")
                        
                        # Tentar buscar todas as câmeras para debug
                        all_cameras = self.supabase.table('cameras').select('id, nome, ordem').execute()
                        if all_cameras.data:
                            log_info("Câmeras disponíveis na tabela:")
                            for cam in all_cameras.data:
                                log_info(f"  - {cam['nome']} (ID: {cam['id']}, Ordem: {cam['ordem']})")
                        else:
                            log_warning("Nenhuma câmera encontrada na tabela cameras")
                        
                        return {'success': False, 'error': f'Câmera não encontrada: {camera_id}', 'message': 'Camera ID não existe na tabela cameras'}
                    else:
                        camera_info = camera_response.data[0]
                        log_debug(f"Câmera validada: {camera_info['nome']} (ID: {camera_info['id']}, Ordem: {camera_info['ordem']})")
                        
                except Exception as e:
                    log_warning(f"Não foi possível validar camera_id: {e}")
                    # Continua sem validação se houver erro na consulta
            
            return {'success': True}
            
        except Exception as e:
            return {'success': False, 'error': f'Erro na validação: {e}'}
    
    def _obter_url_assinada(self, bucket_path: str, expiracao_segundos: int = 604800, max_tentativas: int = 3) -> Optional[str]:
        """
        Obtém URL assinada para arquivo no bucket com retry.
        
        Args:
            bucket_path (str): Caminho do arquivo no bucket
            expiracao_segundos (int): Tempo de expiração em segundos (padrão: 7 dias)
            max_tentativas (int): Número máximo de tentativas
        
        Returns:
            str: URL assinada completa ou None se falhar
        """
        for tentativa in range(max_tentativas):
            try:
                if not self.supabase:
                    log_error("Supabase não conectado para gerar URL assinada")
                    return None
                
                # Gera URL assinada válida por 7 dias (604800 segundos)
                signed_url = self.supabase.storage.from_(self.bucket_name).create_signed_url(
                    bucket_path, 
                    expiracao_segundos
                )
                
                # Verificar se a resposta contém URL válida
                url = None
                if signed_url and 'signedURL' in signed_url:
                    url = signed_url['signedURL']
                elif isinstance(signed_url, str) and signed_url.strip():
                    url = signed_url
                
                # Validar se a URL é completa e funcional
                if url and self._validar_url_completa(url):
                    log_debug(f"URL assinada gerada (tentativa {tentativa + 1}): {Path(bucket_path).name}")
                    return url
                else:
                    log_warning(f"URL assinada inválida na tentativa {tentativa + 1}")
                    
            except Exception as e:
                log_warning(f"Erro ao gerar URL assinada (tentativa {tentativa + 1}): {e}")
            
            # Aguardar antes da próxima tentativa (exceto na última)
            if tentativa < max_tentativas - 1:
                delay = 1.0 * (tentativa + 1)  # 1s, 2s, 3s...
                log_debug(f"Aguardando {delay}s antes da próxima tentativa...")
                time.sleep(delay)
        
        # Todas as tentativas falharam
        log_error(f"Falha ao gerar URL assinada após {max_tentativas} tentativas para: {bucket_path}")
        return None
    
    def _validar_url_completa(self, url: str) -> bool:
        """
        Valida se a URL é completa e funcional.
        
        Args:
            url (str): URL para validar
            
        Returns:
            bool: True se a URL é válida
        """
        if not url or not isinstance(url, str):
            return False
        
        url = url.strip()
        
        # Verificar se começa com https://
        if not url.startswith('https://'):
            return False
        
        # Verificar se contém o domínio do Supabase
        if 'supabase.co' not in url:
            return False
        
        # Verificar se contém token
        if '?token=' not in url:
            return False
        
        # Verificar se não é uma URL de fallback
        if url.startswith('supabase://bucket/'):
            return False
        
        return True
    
    def _inserir_com_retry(self, replay_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insere registro na tabela replays com sistema de retry.
        
        Args:
            replay_data (dict): Dados do replay para inserir
            
        Returns:
            dict: Resultado da operação
        """
        last_error = None
        
        for tentativa in range(self.max_retries + 1):
            try:
                if not self.supabase:
                    return {'success': False, 'error': 'Supabase não conectado'}
                
                # Tentativa de inserção
                response = self.supabase.table('replays').insert(replay_data).execute()
                
                if response.data:
                    replay_inserido = response.data[0]
                    log_success(f"Registro replay inserido (tentativa {tentativa + 1})")
                    log_debug(f"Replay ID: {replay_inserido['id']}")
                    return {
                        'success': True,
                        'replay_id': replay_inserido['id'],
                        'data': replay_inserido,
                        'tentativa': tentativa + 1
                    }
                else:
                    last_error = "Resposta vazia do Supabase"
                    
            except Exception as e:
                last_error = str(e)
                log_warning(f"Tentativa {tentativa + 1} falhou: {e}")
            
            # Aguardar antes da próxima tentativa (exceto na última)
            if tentativa < self.max_retries:
                delay = self.retry_delay_base * (self.retry_backoff_multiplier ** tentativa)
                log_debug(f"Aguardando {delay:.1f}s antes da próxima tentativa...")
                time.sleep(delay)
        
        # Todas as tentativas falharam
        log_error(f"Falha após {self.max_retries + 1} tentativas: {last_error}")
        return {'success': False, 'error': last_error, 'tentativas': self.max_retries + 1}
    
    def insert_replay_record(self, camera_id: str, video_url: str, timestamp_video: datetime, bucket_path: str) -> Dict[str, Any]:
        """
        Insere um novo registro na tabela replays apenas com URLs completas.
        
        Args:
            camera_id (str): UUID da câmera
            video_url (str): URL do vídeo original (deve ser URL completa)
            timestamp_video (datetime): Momento da gravação (deve estar em UTC)
            bucket_path (str): Caminho no bucket para gerar URL assinada
            
        Returns:
            dict: Resultado da operação
        """
        try:
            log_info(f"Inserindo registro replay para câmera: {camera_id[:8]}...")
            
            # ETAPA 1: Validação de dados
            validacao = self._validar_dados_replay(camera_id, video_url, timestamp_video, bucket_path)
            if not validacao['success']:
                log_error(f"Validação do registro replay falhou: {validacao['error']}")
                return validacao
            
            # ETAPA 2: Validar se video_url é uma URL completa
            if not self._validar_url_completa(video_url):
                log_error(f"video_url não é uma URL completa: {video_url}")
                return {'success': False, 'error': 'video_url deve ser uma URL completa e funcional'}
            
            # ETAPA 3: Gerar URL assinada (deve ser completa)
            signed_url = self._obter_url_assinada(bucket_path)
            if not signed_url:
                log_error("Não foi possível gerar URL assinada válida - abortando inserção")
                return {'success': False, 'error': 'Falha ao gerar URL assinada válida'}
            
            # ETAPA 4: Validar URL assinada gerada
            if not self._validar_url_completa(signed_url):
                log_error(f"URL assinada gerada não é válida: {signed_url}")
                return {'success': False, 'error': 'URL assinada gerada não é válida'}
            
            # ETAPA 5: Preparar dados para inserção (ambas URLs são completas)
            replay_data = {
                'video_url': video_url,  # URL completa
                'timestamp_video': timestamp_video.isoformat(),
                'status_envio': 'concluido',  # Upload já foi verificado como bem-sucedido
                'camera_id': camera_id,
                'public_video_url': signed_url,  # URL assinada completa
                'watermark_status': 'pending',  # Marca d'água pendente
                'created_at': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            log_debug(f"Dados preparados para inserção: camera_id={camera_id}, status=concluido")
            log_debug(f"video_url: {video_url[:50]}...")
            log_debug(f"public_video_url: {signed_url[:50]}...")
            
            # ETAPA 6: Inserir com retry
            resultado = self._inserir_com_retry(replay_data)
            
            if resultado['success']:
                log_success(f"Registro replay criado com sucesso: {resultado['replay_id'][:8]}...")
                log_success("Ambas as URLs são completas e funcionais")
            
            return resultado
            
        except Exception as e:
            log_error(f"Erro ao inserir registro replay: {e}")
            return {'success': False, 'error': f'Erro inesperado: {e}'}
    
    def update_public_video_url(self, replay_id: str, public_video_url: str, watermark_status: str = 'completed') -> Dict[str, Any]:
        """
        Atualiza a URL pública do vídeo quando a marca d'água estiver pronta.
        
        Args:
            replay_id (str): ID do registro replay
            public_video_url (str): URL do vídeo com marca d'água
            watermark_status (str): Status da marca d'água ('completed', 'failed')
            
        Returns:
            dict: Resultado da operação
        """
        try:
            if not self.supabase:
                return {'success': False, 'error': 'Supabase não conectado'}
            
            log_info(f"Atualizando URL pública para replay: {replay_id}")
            
            # Dados para atualização
            update_data = {
                'public_video_url': public_video_url,
                'watermark_status': watermark_status,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            # Atualizar registro
            response = self.supabase.table('replays').update(update_data).eq('id', replay_id).execute()
            
            if response.data:
                log_success(f"URL pública atualizada para replay: {replay_id}")
                return {'success': True, 'data': response.data[0]}
            else:
                log_error(f"Falha ao atualizar URL pública - replay não encontrado: {replay_id}")
                return {'success': False, 'error': 'Replay não encontrado'}
                
        except Exception as e:
            log_error(f"Erro ao atualizar URL pública: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_replays_by_camera(self, camera_id: str, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Busca replays por câmera e período.
        
        Args:
            camera_id (str): UUID da câmera
            start_date (datetime, optional): Data de início (deve estar em UTC)
            end_date (datetime, optional): Data de fim (deve estar em UTC)
            
        Returns:
            dict: Resultado da busca
        """
        try:
            if not self.supabase:
                return {'success': False, 'error': 'Supabase não conectado'}
            
            log_info(f"Buscando replays para câmera: {camera_id}")
            
            # Construir query
            query = self.supabase.table('replays').select('*').eq('camera_id', camera_id)
            
            # Filtros de data
            if start_date:
                query = query.gte('timestamp_video', start_date.isoformat())
            if end_date:
                query = query.lte('timestamp_video', end_date.isoformat())
            
            # Ordenar por timestamp mais recente
            query = query.order('timestamp_video', desc=True)
            
            # Executar query
            response = query.execute()
            
            if response.data is not None:
                log_success(f"Encontrados {len(response.data)} replays para câmera: {camera_id}")
                return {'success': True, 'replays': response.data, 'count': len(response.data)}
            else:
                log_info(f"Nenhum replay encontrado para câmera: {camera_id}")
                return {'success': True, 'replays': [], 'count': 0}
                
        except Exception as e:
            log_error(f"Erro ao buscar replays: {e}")
            return {'success': False, 'error': str(e)}
    
    def update_replay_status(self, replay_id: str, status: str, error_message: Optional[str] = None) -> Dict[str, Any]:
        """
        Atualiza o status de um replay.
        
        Args:
            replay_id (str): ID do registro replay
            status (str): Novo status ('pendente', 'processando', 'concluido', 'erro')
            error_message (str, optional): Mensagem de erro se status for 'erro'
            
        Returns:
            dict: Resultado da operação
        """
        try:
            if not self.supabase:
                return {'success': False, 'error': 'Supabase não conectado'}
            
            log_info(f"Atualizando status do replay {replay_id} para: {status}")
            
            # Dados para atualização
            update_data = {
                'status': status,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            # Adicionar mensagem de erro se fornecida
            if error_message and status == 'erro':
                update_data['error_message'] = error_message
            
            # Atualizar registro
            response = self.supabase.table('replays').update(update_data).eq('id', replay_id).execute()
            
            if response.data:
                log_success(f"Status atualizado para replay: {replay_id}")
                return {'success': True, 'data': response.data[0]}
            else:
                log_error(f"Falha ao atualizar status - replay não encontrado: {replay_id}")
                return {'success': False, 'error': 'Replay não encontrado'}
                
        except Exception as e:
            log_error(f"Erro ao atualizar status do replay: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_replay_stats(self) -> Dict[str, Any]:
        """
        Obtém estatísticas dos replays.
        
        Returns:
            dict: Estatísticas dos replays
        """
        try:
            if not self.supabase:
                return {'success': False, 'error': 'Supabase não conectado'}
            
            log_info("Obtendo estatísticas dos replays")
            
            # Buscar todos os replays
            response = self.supabase.table('replays').select('status_envio, watermark_status, created_at').execute()
            
            if response.data is not None:
                replays = response.data
                total = len(replays)
                
                # Contar por status
                status_counts = {}
                watermark_counts = {}
                
                for replay in replays:
                    status = replay.get('status_envio', 'unknown')
                    watermark = replay.get('watermark_status', 'unknown')
                    
                    status_counts[status] = status_counts.get(status, 0) + 1
                    watermark_counts[watermark] = watermark_counts.get(watermark, 0) + 1
                
                stats = {
                    'total_replays': total,
                    'status_distribution': status_counts,
                    'watermark_distribution': watermark_counts
                }
                
                log_success(f"Estatísticas obtidas: {total} replays total")
                return {'success': True, 'stats': stats}
            else:
                return {'success': True, 'stats': {'total_replays': 0}}
                
        except Exception as e:
            log_error(f"Erro ao obter estatísticas: {e}")
            return {'success': False, 'error': str(e)}
