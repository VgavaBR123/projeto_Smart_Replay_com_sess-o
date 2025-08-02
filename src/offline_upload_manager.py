import os
import sqlite3
import threading
import time
import json
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from system_logger import log_info, log_success, log_warning, log_error, log_debug
from network_checker import NetworkConnectivityChecker
from supabase_manager import SupabaseManager


class OfflineUploadManager:
    """
    Gerencia uploads automáticos de vídeos quando a conectividade é restaurada.
    Monitora continuamente a conectividade e processa fila de uploads pendentes.
    """
    
    def __init__(self, db_path: str = None, config_env_path: str = None):
        self.db_path = db_path or os.path.join(os.getcwd(), 'offline_data', 'upload_queue.db')
        self.config_env_path = config_env_path or os.path.join(os.getcwd(), 'config.env')
        
        # Configurações padrão
        self.max_retry_attempts = int(os.getenv('OFFLINE_MAX_RETRY_ATTEMPTS', '5'))
        self.retry_delay_base = int(os.getenv('OFFLINE_RETRY_DELAY_BASE', '60'))  # segundos
        self.connectivity_check_interval = int(os.getenv('OFFLINE_CONNECTIVITY_CHECK_INTERVAL', '30'))  # segundos
        self.upload_batch_size = int(os.getenv('OFFLINE_UPLOAD_BATCH_SIZE', '3'))
        self.max_queue_size = int(os.getenv('OFFLINE_MAX_QUEUE_SIZE', '1000'))
        self.expiration_hours = int(os.getenv('OFFLINE_EXPIRATION_HOURS', '168'))  # 7 dias
        
        # Componentes
        self.network_checker = NetworkConnectivityChecker()
        self.supabase_manager = None
        
        # Threading
        self._running = False
        self._monitor_thread = None
        self._upload_lock = threading.Lock()
        
        # Estatísticas
        self.stats = {
            'total_processed': 0,
            'successful_uploads': 0,
            'failed_uploads': 0,
            'last_cleanup': None,
            'last_connectivity_check': None
        }
        
        self._initialize_database()
        self._load_supabase_manager()
        
    def _load_supabase_manager(self):
        """Carrega o SupabaseManager com configurações do ambiente."""
        try:
            self.supabase_manager = SupabaseManager()
            log_success("✅ SupabaseManager carregado com sucesso")
        except Exception as e:
            log_error(f"❌ Erro ao carregar SupabaseManager: {e}")
            self.supabase_manager = None
    
    def _initialize_database(self):
        """Inicializa o banco de dados SQLite para a fila de uploads."""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Tabela principal de fila de uploads
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS upload_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        video_path TEXT NOT NULL,
                        camera_id TEXT NOT NULL,
                        session_id TEXT,
                        timestamp_created TEXT NOT NULL,
                        file_size INTEGER,
                        checksum TEXT,
                        priority INTEGER DEFAULT 1,
                        status TEXT DEFAULT 'pending',
                        retry_count INTEGER DEFAULT 0,
                        last_attempt TEXT,
                        error_message TEXT,
                        supabase_url TEXT,
                        bucket_path TEXT,
                        arena TEXT,
                        quadra TEXT
                    )
                ''')
                
                # Tabela de configurações
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS upload_config (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                ''')
                
                # Tabela de log de conectividade
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS connectivity_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        status TEXT NOT NULL,
                        latency REAL,
                        error_details TEXT
                    )
                ''')
                
                conn.commit()
                
                # Executa migrações se necessário
                self._run_migrations(conn)
                
                log_success("✅ Banco de dados inicializado com sucesso")
                
        except Exception as e:
            log_error(f"❌ Erro ao inicializar banco de dados: {e}")
            raise
    
    def _run_migrations(self, conn):
        """Executa migrações necessárias no banco de dados."""
        try:
            cursor = conn.cursor()
            
            # Verifica se as colunas arena e quadra existem
            cursor.execute("PRAGMA table_info(upload_queue)")
            columns = cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            # Adiciona coluna 'arena' se não existir
            if 'arena' not in column_names:
                log_info("🔄 Migração: Adicionando coluna 'arena'")
                cursor.execute("ALTER TABLE upload_queue ADD COLUMN arena TEXT")
                
            # Adiciona coluna 'quadra' se não existir
            if 'quadra' not in column_names:
                log_info("🔄 Migração: Adicionando coluna 'quadra'")
                cursor.execute("ALTER TABLE upload_queue ADD COLUMN quadra TEXT")
                
            conn.commit()
            
        except Exception as e:
            log_warning(f"⚠️ Erro durante migrações: {e}")
    
    def add_to_queue(self, video_path: str, camera_id: str, bucket_path: str, 
                     session_id: str = None, arena: str = None, quadra: str = None,
                     priority: int = 1) -> bool:
        """Adiciona um vídeo à fila de upload offline."""
        try:
            if not os.path.exists(video_path):
                log_error(f"❌ Arquivo não encontrado: {video_path}")
                return False
            
            file_size = os.path.getsize(video_path)
            timestamp_created = datetime.now(timezone.utc).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Verifica se já existe na fila
                cursor.execute(
                    "SELECT id FROM upload_queue WHERE video_path = ? AND status = 'pending'",
                    (video_path,)
                )
                
                if cursor.fetchone():
                    log_warning(f"⚠️ Vídeo já está na fila: {os.path.basename(video_path)}")
                    return True
                
                # Adiciona à fila
                cursor.execute('''
                    INSERT INTO upload_queue 
                    (video_path, camera_id, session_id, timestamp_created, file_size, 
                     bucket_path, arena, quadra, priority, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                ''', (video_path, camera_id, session_id, timestamp_created, file_size,
                      bucket_path, arena, quadra, priority))
                
                conn.commit()
                
            log_success(f"✅ Vídeo adicionado à fila offline: {os.path.basename(video_path)}")
            return True
            
        except Exception as e:
            log_error(f"❌ Erro ao adicionar vídeo à fila: {e}")
            return False
    
    def start_monitoring(self):
        """Inicia o monitoramento contínuo de conectividade e processamento da fila."""
        if self._running:
            log_warning("⚠️ Monitoramento já está em execução")
            return
        
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        
        log_info("🔄 Monitoramento de upload offline iniciado")
    
    def stop_monitoring(self):
        """Para o monitoramento de conectividade."""
        self._running = False
        
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        
        log_info("⏹️ Monitoramento de upload offline parado")
    
    def _monitor_loop(self):
        """Loop principal de monitoramento."""
        log_info("🔄 Iniciando loop de monitoramento offline")
        
        while self._running:
            try:
                # Verifica conectividade
                is_connected = self._check_connectivity()
                
                if is_connected:
                    # Processa fila de uploads
                    self._process_upload_queue()
                    
                    # Limpeza periódica
                    self._cleanup_old_entries()
                
                # Aguarda próxima verificação
                time.sleep(self.connectivity_check_interval)
                
            except Exception as e:
                log_error(f"❌ Erro no loop de monitoramento: {e}")
                time.sleep(self.connectivity_check_interval)
    
    def _check_connectivity(self) -> Dict:
        """Verifica conectividade com internet e Supabase."""
        try:
            connectivity_result = self.network_checker.check_full_connectivity()
            
            # Log da conectividade
            self._log_connectivity(connectivity_result)
            
            self.stats['last_connectivity_check'] = datetime.now(timezone.utc).isoformat()
            
            return {
                'internet': connectivity_result.get('internet_accessible', False),
                'supabase': connectivity_result.get('supabase_accessible', False)
            }
            
        except Exception as e:
            log_error(f"❌ Erro ao verificar conectividade: {e}")
            return {'internet': False, 'supabase': False}
    
    def _log_connectivity(self, connectivity_result: Dict):
        """Registra status de conectividade no banco."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                status = 'online' if connectivity_result.get('supabase_accessible') else 'offline'
                error_details = connectivity_result.get('error', '')
                
                # Inserir apenas com colunas básicas para evitar problemas de schema
                cursor.execute('''
                    INSERT INTO connectivity_log (timestamp, status, error_details)
                    VALUES (?, ?, ?)
                ''', (datetime.now(timezone.utc).isoformat(), status, error_details))
                
                conn.commit()
                
        except Exception as e:
            log_error(f"❌ Erro ao registrar conectividade: {e}")
    
    def _process_upload_queue(self):
        """Processa a fila de uploads pendentes."""
        with self._upload_lock:
            try:
                pending_uploads = self._get_pending_uploads()
                
                if not pending_uploads:
                    return
                
                log_info(f"🔄 Processando {len(pending_uploads)} uploads pendentes")
                
                # Processa uploads em paralelo
                with ThreadPoolExecutor(max_workers=self.upload_batch_size) as executor:
                    future_to_upload = {
                        executor.submit(self._process_single_upload, upload): upload
                        for upload in pending_uploads[:self.upload_batch_size]
                    }
                    
                    for future in as_completed(future_to_upload):
                        upload = future_to_upload[future]
                        try:
                            success = future.result()
                            if success:
                                self.stats['successful_uploads'] += 1
                            else:
                                self.stats['failed_uploads'] += 1
                                
                        except Exception as e:
                            log_error(f"❌ Erro no upload de {upload['video_path']}: {e}")
                            self.stats['failed_uploads'] += 1
                
                self.stats['total_processed'] += len(pending_uploads[:self.upload_batch_size])
                
            except Exception as e:
                log_error(f"❌ Erro ao processar fila de uploads: {e}")
    
    def _get_pending_uploads(self) -> List[Dict]:
        """Obtém uploads pendentes da fila, ordenados por prioridade e data."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT id, video_path, camera_id, session_id, bucket_path, 
                           arena, quadra, retry_count, priority
                    FROM upload_queue 
                    WHERE status = 'pending' AND retry_count < ?
                    ORDER BY priority DESC, timestamp_created ASC
                    LIMIT ?
                ''', (self.max_retry_attempts, self.upload_batch_size * 2))
                
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
                
        except Exception as e:
            log_error(f"❌ Erro ao obter uploads pendentes: {e}")
            return []
    
    def _process_single_upload(self, upload: Dict) -> bool:
        """Processa um único upload."""
        video_path = upload['video_path']
        upload_id = upload['id']
        
        try:
            # Verifica se arquivo ainda existe
            if not os.path.exists(video_path):
                self._update_upload_status(upload_id, 'failed', 'Arquivo não encontrado')
                log_warning(f"⚠️ Arquivo não encontrado: {video_path}")
                return False
            
            # Atualiza tentativa
            self._update_upload_attempt(upload_id)
            
            # Realiza upload
            if not self.supabase_manager:
                raise Exception("SupabaseManager não disponível")
            
            upload_result = self.supabase_manager.upload_video_to_bucket(
                video_path, upload['bucket_path']
            )
            
            if upload_result and upload_result.get('success'):
                # Upload bem-sucedido
                self._update_upload_status(upload_id, 'completed', None, upload_result.get('url'))
                
                # Remove arquivo local se configurado
                if os.getenv('OFFLINE_DELETE_AFTER_UPLOAD', 'true').lower() == 'true':
                    try:
                        os.remove(video_path)
                        log_debug(f"🗑️ Arquivo local removido: {os.path.basename(video_path)}")
                    except Exception as e:
                        log_warning(f"⚠️ Erro ao remover arquivo local: {e}")
                
                log_success(f"✅ Upload concluído: {os.path.basename(video_path)}")
                return True
            else:
                # Upload falhou
                error_msg = upload_result.get('error', 'Erro desconhecido') if upload_result else 'Upload falhou'
                self._update_upload_status(upload_id, 'pending', error_msg)
                log_error(f"❌ Falha no upload: {os.path.basename(video_path)} - {error_msg}")
                return False
                
        except Exception as e:
            error_msg = str(e)
            self._update_upload_status(upload_id, 'pending', error_msg)
            log_error(f"❌ Erro no upload de {os.path.basename(video_path)}: {error_msg}")
            return False
    
    def _update_upload_attempt(self, upload_id: int):
        """Atualiza contador de tentativas de upload."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE upload_queue 
                    SET retry_count = retry_count + 1, last_attempt = ?
                    WHERE id = ?
                ''', (datetime.now(timezone.utc).isoformat(), upload_id))
                
                conn.commit()
                
        except Exception as e:
            log_error(f"❌ Erro ao atualizar tentativa de upload: {e}")
    
    def _update_upload_status(self, upload_id: int, status: str, error_message: str = None, 
                             supabase_url: str = None):
        """Atualiza status de um upload na fila."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE upload_queue 
                    SET status = ?, error_message = ?, supabase_url = ?, last_attempt = ?
                    WHERE id = ?
                ''', (status, error_message, supabase_url, 
                      datetime.now(timezone.utc).isoformat(), upload_id))
                
                conn.commit()
                
        except Exception as e:
            log_error(f"❌ Erro ao atualizar status de upload: {e}")
    
    def _cleanup_old_entries(self):
        """Remove entradas antigas e concluídas da fila."""
        try:
            # Verifica se precisa fazer limpeza
            last_cleanup = self.stats.get('last_cleanup')
            if last_cleanup:
                last_cleanup_dt = datetime.fromisoformat(last_cleanup.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) - last_cleanup_dt < timedelta(hours=24):
                    return
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Remove uploads concluídos há mais de X horas
                expiration_time = datetime.now(timezone.utc) - timedelta(hours=self.expiration_hours)
                
                cursor.execute('''
                    DELETE FROM upload_queue 
                    WHERE status = 'completed' AND timestamp_created < ?
                ''', (expiration_time.isoformat(),))
                
                completed_removed = cursor.rowcount
                
                # Remove uploads que excederam tentativas máximas
                cursor.execute('''
                    DELETE FROM upload_queue 
                    WHERE status = 'pending' AND retry_count >= ?
                ''', (self.max_retry_attempts,))
                
                failed_removed = cursor.rowcount
                
                # Limpa logs de conectividade antigos
                cursor.execute('''
                    DELETE FROM connectivity_log 
                    WHERE timestamp < ?
                ''', (expiration_time.isoformat(),))
                
                logs_removed = cursor.rowcount
                
                conn.commit()
                
                if completed_removed > 0 or failed_removed > 0 or logs_removed > 0:
                    log_info(f"🧹 Limpeza concluída: {completed_removed} concluídos, "
                            f"{failed_removed} falhados, {logs_removed} logs removidos")
                
                self.stats['last_cleanup'] = datetime.now(timezone.utc).isoformat()
                
        except Exception as e:
            log_error(f"❌ Erro na limpeza de entradas antigas: {e}")
    
    def get_queue_status(self) -> Dict:
        """Retorna status atual da fila de uploads."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Contadores por status
                cursor.execute('''
                    SELECT status, COUNT(*) as count 
                    FROM upload_queue 
                    GROUP BY status
                ''')
                
                status_counts = dict(cursor.fetchall())
                
                # Tamanho total da fila
                cursor.execute('SELECT COUNT(*) FROM upload_queue')
                total_queue_size = cursor.fetchone()[0]
                
                # Uploads recentes (últimas 24h)
                recent_time = datetime.now(timezone.utc) - timedelta(hours=24)
                cursor.execute('''
                    SELECT COUNT(*) FROM upload_queue 
                    WHERE timestamp_created > ?
                ''', (recent_time.isoformat(),))
                
                recent_uploads = cursor.fetchone()[0]
                
                return {
                    'queue_size': total_queue_size,
                    'pending': status_counts.get('pending', 0),
                    'completed': status_counts.get('completed', 0),
                    'failed': status_counts.get('failed', 0),
                    'recent_uploads_24h': recent_uploads,
                    'is_monitoring': self._running,
                    'stats': self.stats.copy()
                }
                
        except Exception as e:
            log_error(f"❌ Erro ao obter status da fila: {e}")
            return {
                'queue_size': 0,
                'pending': 0,
                'completed': 0,
                'failed': 0,
                'recent_uploads_24h': 0,
                'is_monitoring': self._running,
                'stats': self.stats.copy()
            }
    
    def force_process_queue(self) -> Dict:
        """Força o processamento da fila de uploads (para testes/debug)."""
        log_info("🔄 Forçando processamento da fila de uploads")
        
        if not self._check_connectivity():
            return {'success': False, 'error': 'Sem conectividade'}
        
        self._process_upload_queue()
        
        return {
            'success': True,
            'queue_status': self.get_queue_status()
        }


# Instância global para uso em outros módulos
_global_upload_manager = None

def get_upload_manager() -> OfflineUploadManager:
    """Retorna instância global do OfflineUploadManager."""
    global _global_upload_manager
    
    if _global_upload_manager is None:
        _global_upload_manager = OfflineUploadManager()
        _global_upload_manager.start_monitoring()
    
    return _global_upload_manager


if __name__ == "__main__":
    # Teste básico
    manager = OfflineUploadManager()
    
    print("Status da fila:")
    print(json.dumps(manager.get_queue_status(), indent=2))
    
    # Inicia monitoramento
    manager.start_monitoring()
    
    try:
        # Mantém rodando por 60 segundos para teste
        time.sleep(60)
    except KeyboardInterrupt:
        print("\nParando monitoramento...")
    finally:
        manager.stop_monitoring()