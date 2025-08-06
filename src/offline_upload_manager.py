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
from replay_manager import ReplayManager


class OfflineUploadManager:
    """
    Gerencia uploads automáticos de vídeos quando a conectividade é restaurada.
    Monitora continuamente a conectividade e processa fila de uploads pendentes.
    """
    
    def __init__(self, db_path: str = None, config_env_path: str = None):
        # Garantir que offline_data seja criado na raiz do projeto
        if db_path is None:
            from pathlib import Path
            project_root = Path(__file__).parent.parent
            offline_data_dir = project_root / "offline_data"
            offline_data_dir.mkdir(exist_ok=True)
            self.db_path = str(offline_data_dir / "upload_queue.db")
        else:
            self.db_path = db_path
            
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
        self.replay_manager = None
        
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
        self._initialize_replay_manager()
        
    def _load_supabase_manager(self):
        """Carrega o SupabaseManager com configurações do ambiente."""
        try:
            self.supabase_manager = SupabaseManager()
            log_success("✅ SupabaseManager carregado com sucesso")
        except Exception as e:
            log_error(f"❌ Erro ao carregar SupabaseManager: {e}")
            self.supabase_manager = None
    
    def _initialize_replay_manager(self):
        """Inicializa ReplayManager após SupabaseManager."""
        try:
            if self.supabase_manager and self.supabase_manager.supabase:
                self.replay_manager = ReplayManager(supabase_manager=self.supabase_manager)
                log_success("✅ ReplayManager inicializado no OfflineUploadManager")
            else:
                log_warning("⚠️ SupabaseManager não disponível para ReplayManager")
        except Exception as e:
            log_error(f"❌ Erro ao inicializar ReplayManager: {e}")
    
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
            
            # NOVA MIGRAÇÃO: Tornar checksum opcional
            # Verificar se checksum é NOT NULL
            checksum_info = next((col for col in columns if col[1] == 'checksum'), None)
            if checksum_info and checksum_info[3] == 1:  # notNull = 1
                log_info("🔄 Migração: Tornando checksum opcional")
                # Criar nova tabela sem NOT NULL no checksum
                cursor.execute('''
                    CREATE TABLE upload_queue_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        video_path TEXT NOT NULL,
                        camera_id TEXT NOT NULL,
                        session_id TEXT,
                        timestamp_created TEXT NOT NULL,
                        file_size INTEGER,
                        checksum TEXT,  -- SEM NOT NULL
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
                
                # Copiar dados existentes
                cursor.execute('''
                    INSERT INTO upload_queue_new 
                    SELECT * FROM upload_queue
                ''')
                
                # Substituir tabela antiga
                cursor.execute('DROP TABLE upload_queue')
                cursor.execute('ALTER TABLE upload_queue_new RENAME TO upload_queue')
                
                log_success("✅ Migração concluída: checksum agora é opcional")
            
            # Adiciona coluna 'arena' se não existir
            if 'arena' not in column_names:
                log_info("🔄 Migração: Adicionando coluna 'arena'")
                cursor.execute("ALTER TABLE upload_queue ADD COLUMN arena TEXT")
                
            # Adiciona coluna 'quadra' se não existir
            if 'quadra' not in column_names:
                log_info("🔄 Migração: Adicionando coluna 'quadra'")
                cursor.execute("ALTER TABLE upload_queue ADD COLUMN quadra TEXT")
            
            # NOVA MIGRAÇÃO: Campos para registro replay
            if 'timestamp_video' not in column_names:
                log_info("🔄 Migração: Adicionando coluna 'timestamp_video'")
                cursor.execute("ALTER TABLE upload_queue ADD COLUMN timestamp_video TEXT")
            
            if 'camera_uuid' not in column_names:
                log_info("🔄 Migração: Adicionando coluna 'camera_uuid'")
                cursor.execute("ALTER TABLE upload_queue ADD COLUMN camera_uuid TEXT")
            
            if 'replay_inserted' not in column_names:
                log_info("🔄 Migração: Adicionando coluna 'replay_inserted'")
                cursor.execute("ALTER TABLE upload_queue ADD COLUMN replay_inserted INTEGER DEFAULT 0")
                
            conn.commit()
            
        except Exception as e:
            log_warning(f"⚠️ Erro durante migrações: {e}")
    
    def add_to_queue(self, video_path: str, camera_id: str, bucket_path: str, 
                     session_id: str = None, arena: str = None, quadra: str = None,
                     priority: int = 1, timestamp_video: str = None, 
                     camera_uuid: str = None) -> bool:
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
                     bucket_path, arena, quadra, priority, status, timestamp_video, 
                     camera_uuid, replay_inserted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0)
                ''', (video_path, camera_id, session_id, timestamp_created, file_size,
                      bucket_path, arena, quadra, priority, timestamp_video, camera_uuid))
                
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
                           arena, quadra, retry_count, priority, timestamp_video, 
                           camera_uuid, replay_inserted
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
                
                # Inserir registro replay se necessário
                if upload.get('replay_inserted', 0) == 0 and upload.get('timestamp_video') and upload.get('camera_uuid'):
                    success_replay = self._insert_replay_record(upload, upload_result.get('url'))
                    if success_replay:
                        self._mark_replay_inserted(upload_id)
                
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
    
    def _insert_replay_record(self, upload: Dict, video_url: str) -> bool:
        """Insere registro replay após upload bem-sucedido."""
        try:
            if not self.replay_manager:
                log_warning("⚠️ ReplayManager não disponível")
                return False
            
            camera_uuid = upload.get('camera_uuid')
            timestamp_video = upload.get('timestamp_video')
            bucket_path = upload.get('bucket_path')
            
            if not all([camera_uuid, timestamp_video, bucket_path]):
                log_warning("⚠️ Dados insuficientes para registro replay")
                return False
            
            # Gerar URL assinada se não tiver
            if not video_url or not self._validar_url_completa(video_url):
                try:
                    from hierarchical_video_manager import HierarchicalVideoManager
                    video_manager = HierarchicalVideoManager()
                    if video_manager.conectar_supabase():
                        signed_url = video_manager._obter_url_assinada(bucket_path)
                        if signed_url and self._validar_url_completa(signed_url):
                            video_url = signed_url
                        else:
                            log_error("❌ Falha ao gerar URL assinada")
                            return False
                    else:
                        log_error("❌ Falha ao conectar video_manager")
                        return False
                except Exception as e:
                    log_error(f"❌ Erro ao gerar URL assinada: {e}")
                    return False
            
            # Converter timestamp para datetime
            from datetime import datetime, timezone
            try:
                timestamp_dt = datetime.fromisoformat(timestamp_video.replace('Z', '+00:00'))
            except:
                log_error("❌ Erro ao converter timestamp_video")
                return False
            
            # Inserir registro replay
            result = self.replay_manager.insert_replay_record(
                camera_id=camera_uuid,
                video_url=video_url,
                timestamp_video=timestamp_dt,
                bucket_path=bucket_path
            )
            
            if result.get('success'):
                log_success(f"✅ Registro replay inserido: {upload.get('camera_id')}")
                return True
            else:
                log_error(f"❌ Falha no registro replay: {result.get('error')}")
                return False
                
        except Exception as e:
            log_error(f"❌ Erro na inserção replay: {e}")
            return False
    
    def _mark_replay_inserted(self, upload_id: int):
        """Marca que o replay foi inserido para este upload."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE upload_queue 
                    SET replay_inserted = 1
                    WHERE id = ?
                ''', (upload_id,))
                
                conn.commit()
                log_debug(f"✅ Replay marcado como inserido para upload ID: {upload_id}")
                
        except Exception as e:
            log_error(f"❌ Erro ao marcar replay como inserido: {e}")
    
    def _validar_url_completa(self, url):
        """Valida se URL é completa e funcional."""
        if not url or not isinstance(url, str):
            return False
        url = url.strip()
        return (url.startswith('https://') and
                'supabase.co' in url and
                '?token=' in url and
                not url.startswith('supabase://bucket/'))
    
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