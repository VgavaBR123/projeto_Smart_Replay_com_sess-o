#!/usr/bin/env python3
"""
Sistema de Gravação de Câmeras IP com Buffer Circular
Grava os últimos 25 segundos diretamente em formato otimizado quando a tecla 'S' é pressionada
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Carregar configurações do config.env ANTES de tudo
config_path = Path(__file__).parent.parent / "config.env"
if config_path.exists():
    load_dotenv(config_path)
    print(f"📋 Configurações carregadas de: {config_path}")
else:
    print(f"⚠️ Arquivo config.env não encontrado em: {config_path}")

# Configurações para suprimir avisos do FFmpeg/OpenCV - DEVE ser antes de importar cv2
os.environ['OPENCV_FFMPEG_LOGLEVEL'] = '-8'  # Suprimir logs do FFmpeg
os.environ['OPENCV_LOG_LEVEL'] = 'ERROR'     # Apenas erros críticos do OpenCV
os.environ['OPENCV_VIDEOIO_DEBUG'] = '0'     # Desabilitar debug do VideoIO
os.environ['FFMPEG_LOG_LEVEL'] = 'quiet'     # FFmpeg silencioso

import cv2
import numpy as np
import threading
import time
import queue
from datetime import datetime, timezone
from collections import deque

# Configurar OpenCV para suprimir logs
cv2.setLogLevel(0)  # Suprimir logs do OpenCV

# Importações para Device ID e QR Code
from device_manager import DeviceManager
from qr_generator import QRCodeGenerator

# Importação para informações ONVIF das câmeras
from onvif_device_info import ONVIFDeviceManager

# Importação para gerenciamento do Supabase
from supabase_manager import SupabaseManager

# Importação para gerenciamento de replays
from replay_manager import ReplayManager

# Importação para gerenciamento hierárquico de vídeos
from hierarchical_video_manager import HierarchicalVideoManager

# Importação para sistema de logs limpos
from system_logger import log_info, log_success, log_warning, log_error, log_debug, system_logger

# Importação para sistema de marca d'água
from watermark_manager import WatermarkManager


class CameraRecorder:
    def __init__(self, camera_url, camera_name, fps=30, buffer_seconds=25):
        self.camera_url = camera_url
        self.camera_name = camera_name
        self.fps = fps
        self.buffer_seconds = buffer_seconds
        self.buffer_size = fps * buffer_seconds  # 25 segundos de frames
        
        # Buffer circular para armazenar frames
        self.frame_buffer = deque(maxlen=self.buffer_size)
        self.timestamp_buffer = deque(maxlen=self.buffer_size)
        
        # Threading
        self.capture_thread = None
        self.running = False
        
        # Câmera
        self.cap = None
        self.frame_width = None
        self.frame_height = None
        
        # Lock para thread safety
        self.buffer_lock = threading.Lock()
        self.saving = False  # Flag para pausar verificações durante salvamento
        
        # Sistema de marca d'água
        self.watermark_manager = None
        self._init_watermark_manager()
        
    def connect_camera(self):
        """Conecta à câmera IP"""
        print(f"Conectando à câmera {self.camera_name}: {self.camera_url}")
        
        self.cap = cv2.VideoCapture(self.camera_url)
        
        if not self.cap.isOpened():
            print(f"Erro: Não foi possível conectar à câmera {self.camera_name}")
            return False
            
        # Configurar propriedades da câmera para reduzir latência
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Buffer mínimo
        self.cap.set(cv2.CAP_PROP_FPS, 30)  # Forçar 30 FPS
        
        # Configurações adicionais para RTSP
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
        
        # Obter resolução da câmera
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        
        print(f"Câmera {self.camera_name} conectada:")
        print(f"  Resolução: {self.frame_width}x{self.frame_height}")
        print(f"  FPS: {actual_fps}")
        
        return True
    
    def start_capture(self):
        """Inicia a captura em thread separada"""
        if not self.connect_camera():
            return False
            
        self.running = True
        self.capture_thread = threading.Thread(target=self._capture_loop)
        self.capture_thread.daemon = True
        self.capture_thread.start()
        
        print(f"✅ Captura iniciada para {self.camera_name}")
        print(f"   Buffer configurado para: {self.buffer_seconds}s ({self.buffer_size} frames)")
        
        return True
    
    def stop_capture(self):
        """Para a captura"""
        self.running = False
        if self.capture_thread:
            self.capture_thread.join()
        if self.cap:
            self.cap.release()
    
    def _capture_loop(self):
        """Loop principal de captura de frames"""
        consecutive_errors = 0
        last_health_check = time.time()
        last_buffer_report = time.time()
        buffer_fill_reported = False  # Para reportar quando o buffer estiver cheio pela primeira vez
        
        while self.running:
            ret, frame = self.cap.read()
            
            if ret:
                consecutive_errors = 0
                current_time = time.time()
                
                with self.buffer_lock:
                    self.frame_buffer.append(frame)
                    self.timestamp_buffer.append(current_time)
                    
                    # Manter apenas os frames do buffer configurado
                    while len(self.frame_buffer) > self.buffer_size:
                        self.frame_buffer.popleft()
                        self.timestamp_buffer.popleft()
                    
                    # Reportar quando o buffer estiver cheio pela primeira vez
                    if not buffer_fill_reported and len(self.frame_buffer) >= self.buffer_size * 0.95:
                        buffer_duration = self.timestamp_buffer[-1] - self.timestamp_buffer[0] if len(self.timestamp_buffer) > 1 else 0
                        print(f"🎯 {self.camera_name}: Buffer inicial preenchido - {len(self.frame_buffer)}/{self.buffer_size} frames ({buffer_duration:.1f}s)")
                        buffer_fill_reported = True
                
                # Relatório de status do buffer a cada 30 segundos
                if current_time - last_buffer_report > 30:
                    with self.buffer_lock:
                        buffer_count = len(self.frame_buffer)
                        if len(self.timestamp_buffer) > 1:
                            buffer_duration = self.timestamp_buffer[-1] - self.timestamp_buffer[0]
                            print(f"📊 {self.camera_name}: Buffer atual {buffer_count}/{self.buffer_size} frames ({buffer_duration:.1f}s)")
                        else:
                            print(f"📊 {self.camera_name}: Buffer atual {buffer_count}/{self.buffer_size} frames")
                    last_buffer_report = current_time
                
                # Verificação de saúde do buffer (apenas se não estiver salvando)
                if not self.saving and current_time - last_health_check > 10:
                    self._check_buffer_health()
                    last_health_check = current_time
                    
            else:
                consecutive_errors += 1
                print(f"⚠️  Erro na captura {self.camera_name} (erro #{consecutive_errors})")
                
                # Tentar reconectar após muitos erros
                if consecutive_errors >= 30:
                    print(f"🔄 Tentando reconectar {self.camera_name} após {consecutive_errors} erros...")
                    if self._reconnect_camera():
                        consecutive_errors = 0
                        buffer_fill_reported = False  # Reset para reportar novamente após reconexão
                    else:
                        time.sleep(1)  # Esperar mais tempo se a reconexão falhar
                
                time.sleep(0.033)  # ~30 FPS em caso de erro
    
    def _check_buffer_health(self):
        """Verifica a saúde do buffer e reporta problemas"""
        # Não verificar durante salvamento para evitar interferência
        if self.saving:
            return
            
        with self.buffer_lock:
            if len(self.timestamp_buffer) < 2:
                return
                
            buffer_duration = self.timestamp_buffer[-1] - self.timestamp_buffer[0]
            expected_frames = self.buffer_seconds * self.fps
            current_frames = len(self.frame_buffer)
            
            # Verificar se o buffer está muito abaixo do esperado
            if current_frames < expected_frames * 0.8:  # 80% do esperado
                print(f"⚠️  Câmera {self.camera_name}: Buffer baixo - {current_frames}/{expected_frames} frames ({buffer_duration:.1f}s)")
            elif current_frames >= expected_frames * 0.95:  # Buffer quase cheio
                print(f"✅ Câmera {self.camera_name}: Buffer saudável - {current_frames}/{expected_frames} frames ({buffer_duration:.1f}s)")
    
    def _reconnect_camera(self):
        """Tenta reconectar a câmera"""
        try:
            if self.cap:
                self.cap.release()
            
            print(f"Reconectando câmera {self.camera_name}...")
            self.cap = cv2.VideoCapture(self.camera_url)
            
            if self.cap.isOpened():
                # Reconfigurar propriedades
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self.cap.set(cv2.CAP_PROP_FPS, 30)
                self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
                print(f"✅ Câmera {self.camera_name} reconectada com sucesso")
            else:
                print(f"❌ Falha ao reconectar câmera {self.camera_name}")
                
        except Exception as e:
            print(f"❌ Erro na reconexão da câmera {self.camera_name}: {e}")

    def get_latest_frame(self):
        """Retorna o frame mais recente do buffer"""
        with self.buffer_lock:
            if len(self.frame_buffer) > 0:
                return self.frame_buffer[-1]
        return None
    
    def _init_watermark_manager(self):
        """Inicializa o sistema de marca d'água"""
        try:
            # Verificar se a marca d'água está habilitada
            watermark_enabled = os.getenv('WATERMARK_ENABLED', 'true').lower() == 'true'
            
            if watermark_enabled:
                watermark_path = os.getenv('WATERMARK_PATH', 
                    r"c:\Users\Vinicius\PycharmProjects\Projeto Camera Vai dar Certo\marca_dagua\Smart Byte - Horizontal.png")
                
                self.watermark_manager = WatermarkManager(watermark_path)
                print(f"🎨 [{self.camera_name}] Sistema de marca d'água inicializado")
            else:
                print(f"⚪ [{self.camera_name}] Marca d'água desabilitada")
                
        except Exception as e:
            print(f"❌ [{self.camera_name}] Erro ao inicializar marca d'água: {e}")
            self.watermark_manager = None

    def compress_video_for_upload(self, input_path, output_path):
        """Comprime vídeo usando FFmpeg para upload otimizado"""
        try:
            import subprocess
            
            # Configurações de compressão do config.env
            compression_enabled = os.getenv('VIDEO_COMPRESSION_ENABLED', 'true').lower() == 'true'
            if not compression_enabled:
                print(f"📁 [{self.camera_name}] Compressão desabilitada - usando arquivo original")
                return input_path
            
            crf = int(os.getenv('VIDEO_QUALITY_CRF', '28'))
            bitrate = int(os.getenv('VIDEO_BITRATE_KBPS', '2000'))
            fps = int(os.getenv('VIDEO_FPS_UPLOAD', '15'))
            width = int(os.getenv('VIDEO_SCALE_WIDTH', '1280'))
            height = int(os.getenv('VIDEO_SCALE_HEIGHT', '720'))
            max_size_mb = int(os.getenv('MAX_FILE_SIZE_MB', '50'))
            
            print(f"🗜️ [{self.camera_name}] Comprimindo para upload...")
            print(f"   📐 Resolução: {width}x{height}")
            print(f"   🎬 FPS: {fps}")
            print(f"   📊 CRF: {crf}, Bitrate: {bitrate}k")
            print(f"   📦 Tamanho máximo: {max_size_mb}MB")
            
            # Comando FFmpeg otimizado
            cmd = [
                'ffmpeg', '-y',  # Sobrescrever arquivo se existir
                '-i', input_path,  # Arquivo de entrada
                '-c:v', 'libx264',  # Codec de vídeo
                '-preset', 'fast',  # Preset de velocidade
                '-crf', str(crf),  # Qualidade (18-28 é bom)
                '-maxrate', f'{bitrate}k',  # Bitrate máximo
                '-bufsize', f'{bitrate * 2}k',  # Buffer size
                '-vf', f'scale={width}:{height}',  # Redimensionar
                '-r', str(fps),  # FPS
                '-movflags', '+faststart',  # Otimizar para streaming
                '-loglevel', 'error',  # Apenas erros
                output_path
            ]
            
            start_time = time.time()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            compression_time = time.time() - start_time
            
            if result.returncode == 0:
                if os.path.exists(output_path):
                    original_size = os.path.getsize(input_path) / (1024*1024)
                    compressed_size = os.path.getsize(output_path) / (1024*1024)
                    compression_ratio = (1 - compressed_size/original_size) * 100
                    
                    print(f"✅ [{self.camera_name}] Compressão concluída em {compression_time:.1f}s")
                    print(f"   📊 {original_size:.1f}MB → {compressed_size:.1f}MB ({compression_ratio:.1f}% redução)")
                    
                    # Verificar se está dentro do limite
                    if compressed_size <= max_size_mb:
                        print(f"   ✅ Tamanho dentro do limite ({max_size_mb}MB)")
                        return output_path
                    else:
                        print(f"   ⚠️ Arquivo ainda muito grande ({compressed_size:.1f}MB > {max_size_mb}MB)")
                        # Tentar compressão mais agressiva
                        return self._compress_aggressive(input_path, output_path, max_size_mb)
                else:
                    print(f"❌ [{self.camera_name}] Arquivo comprimido não foi criado")
                    return None
            else:
                print(f"❌ [{self.camera_name}] Erro na compressão FFmpeg:")
                print(f"   {result.stderr}")
                return None
                
        except subprocess.TimeoutExpired:
            print(f"⏰ [{self.camera_name}] Timeout na compressão (5min)")
            return None
        except FileNotFoundError:
            print(f"❌ [{self.camera_name}] FFmpeg não encontrado - usando arquivo original")
            return input_path
        except Exception as e:
            print(f"❌ [{self.camera_name}] Erro na compressão: {e}")
            return None
    
    def _compress_aggressive(self, input_path, output_path, max_size_mb):
        """Compressão mais agressiva se o arquivo ainda estiver muito grande"""
        try:
            import subprocess
            
            print(f"🗜️ [{self.camera_name}] Aplicando compressão agressiva...")
            
            # Configurações mais agressivas
            aggressive_output = output_path.replace('.mp4', '_aggressive.mp4')
            
            cmd = [
                'ffmpeg', '-y',
                '-i', input_path,
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '32',  # Qualidade mais baixa
                '-maxrate', '1000k',  # Bitrate menor
                '-bufsize', '2000k',
                '-vf', 'scale=960:540',  # Resolução menor
                '-r', '12',  # FPS menor
                '-movflags', '+faststart',
                '-loglevel', 'error',
                aggressive_output
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0 and os.path.exists(aggressive_output):
                aggressive_size = os.path.getsize(aggressive_output) / (1024*1024)
                print(f"   📊 Compressão agressiva: {aggressive_size:.1f}MB")
                
                if aggressive_size <= max_size_mb:
                    # Remover arquivo intermediário
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    os.rename(aggressive_output, output_path)
                    print(f"   ✅ Tamanho aceitável após compressão agressiva")
                    return output_path
                else:
                    print(f"   ❌ Ainda muito grande mesmo com compressão agressiva")
                    return None
            else:
                print(f"   ❌ Falha na compressão agressiva")
                return None
                
        except Exception as e:
            print(f"❌ [{self.camera_name}] Erro na compressão agressiva: {e}")
            return None

    def save_last_25_seconds(self, output_path):
        """Salva os últimos 25 segundos diretamente em formato otimizado"""
        print(f"🎬 [{self.camera_name}] Iniciando salvamento otimizado...")
        
        # Marcar que está salvando para pausar verificações
        self.saving = True
        print(f"🔒 [{self.camera_name}] Flag saving ativada")
        
        try:
            print(f"🔄 [{self.camera_name}] Copiando buffer com lock...")
            with self.buffer_lock:
                if len(self.frame_buffer) == 0:
                    print(f"❌ [{self.camera_name}] Buffer vazio")
                    return False
                    
                frames = list(self.frame_buffer)
                timestamps = list(self.timestamp_buffer)
                print(f"📊 [{self.camera_name}] Buffer copiado: {len(frames)} frames, {len(timestamps)} timestamps")
            
            # Verificar se há frames suficientes
            min_frames = self.fps * 5  # Pelo menos 5 segundos
            if len(frames) < min_frames:
                print(f"❌ [{self.camera_name}] Buffer insuficiente: {len(frames)} frames (mínimo: {min_frames})")
                return False
            
            # Calcular tempo real do buffer
            if len(timestamps) > 1:
                buffer_duration = timestamps[-1] - timestamps[0]
                real_fps = len(frames) / buffer_duration if buffer_duration > 0 else 0
                
                # Alertar se o buffer está muito abaixo do esperado
                expected_duration = self.buffer_seconds
                if buffer_duration < expected_duration * 0.8:  # 80% do esperado
                    print(f"⚠️  [{self.camera_name}] Buffer curto - {buffer_duration:.1f}s de {expected_duration}s esperados")
                
                print(f"📊 [{self.camera_name}] {len(frames)} frames, {buffer_duration:.1f}s, FPS real: {real_fps:.1f}")
            
            # Criar pasta se não existir
            print(f"📁 [{self.camera_name}] Criando diretório: {os.path.dirname(output_path)}")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Configurar codec otimizado H.264
            print(f"🎥 [{self.camera_name}] Configurando VideoWriter otimizado...")
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # Usar mp4v em vez de H264 para compatibilidade
            print(f"📐 [{self.camera_name}] Resolução: {self.frame_width}x{self.frame_height}")
            
            # Usar FPS reduzido para arquivo menor
            optimized_fps = int(os.getenv('VIDEO_FPS_UPLOAD', '15'))
            
            # Criar arquivo temporário primeiro
            temp_output = output_path.replace('.mp4', '_temp.mp4')
            out = cv2.VideoWriter(temp_output, fourcc, optimized_fps, 
                                 (self.frame_width, self.frame_height))
            
            if not out.isOpened():
                print(f"❌ [{self.camera_name}] Erro ao criar VideoWriter")
                return False
            
            print(f"✅ [{self.camera_name}] VideoWriter otimizado configurado (MP4V, {optimized_fps} FPS)")
            
            # Calcular step para manter FPS desejado
            frame_step = max(1, int(self.fps / optimized_fps))
            print(f"💾 [{self.camera_name}] Salvando frames otimizados (step: {frame_step})...")
            
            frames_written = 0
            start_time = time.time()
            
            for i in range(0, len(frames), frame_step):
                try:
                    frame = frames[i]
                    if frame is not None:
                        # Verificar timeout (máximo 2 minutos)
                        elapsed = time.time() - start_time
                        if elapsed > 120:  # 2 minutos
                            print(f"⏰ [{self.camera_name}] Timeout após {elapsed:.1f}s - salvando {frames_written} frames")
                            break
                        
                        # Aplicar marca d'água se habilitada
                        if self.watermark_manager:
                            frame = self.watermark_manager.apply_watermark(frame)
                        
                        out.write(frame)
                        frames_written += 1
                        
                        # Progress report a cada 50 frames salvos
                        if frames_written % 50 == 0:
                            progress = (i / len(frames)) * 100
                            elapsed = time.time() - start_time
                            fps_write = frames_written / elapsed if elapsed > 0 else 0
                            watermark_status = "com marca d'água" if self.watermark_manager else "sem marca d'água"
                            print(f"📈 [{self.camera_name}] Progresso: {frames_written} frames salvos ({progress:.1f}%) - {fps_write:.1f} fps escrita ({watermark_status})")
                    else:
                        print(f"⚠️  [{self.camera_name}] Frame {i} é None")
                        
                except Exception as e:
                    print(f"❌ [{self.camera_name}] Erro ao escrever frame {i}: {e}")
                    continue
            
            print(f"🔚 [{self.camera_name}] Finalizando arquivo temporário...")
            total_time = time.time() - start_time
            print(f"⏱️  [{self.camera_name}] Tempo total de escrita: {total_time:.1f}s")
            
            out.release()
            
            # Verificar se o arquivo temporário foi criado
            if os.path.exists(temp_output):
                temp_size = os.path.getsize(temp_output) / (1024*1024)
                print(f"📏 [{self.camera_name}] Arquivo temporário: {temp_size:.1f} MB, Frames: {frames_written}")
                
                # Comprimir para upload se habilitado
                compression_enabled = os.getenv('VIDEO_COMPRESSION_ENABLED', 'true').lower() == 'true'
                if compression_enabled:
                    compressed_path = self.compress_video_for_upload(temp_output, output_path)
                    
                    # Remover arquivo temporário
                    if os.path.exists(temp_output):
                        os.remove(temp_output)
                    
                    if compressed_path and os.path.exists(compressed_path):
                        final_size = os.path.getsize(compressed_path) / (1024*1024)
                        print(f"✅ [{self.camera_name}] Arquivo final comprimido: {final_size:.1f} MB")
                        return True
                    else:
                        print(f"❌ [{self.camera_name}] Falha na compressão")
                        return False
                else:
                    # Apenas renomear arquivo temporário
                    os.rename(temp_output, output_path)
                    print(f"✅ [{self.camera_name}] Arquivo salvo sem compressão: {temp_size:.1f} MB")
                    return True
            else:
                print(f"❌ [{self.camera_name}] Arquivo temporário não foi criado")
                return False
            
        except Exception as e:
            print(f"❌ [{self.camera_name}] Erro durante salvamento: {e}")
            import traceback
            traceback.print_exc()
            return False
            
        finally:
            # Reativar verificações após salvamento
            self.saving = False
            print(f"🔓 [{self.camera_name}] Flag saving desativada")

class CameraSystem:
    def __init__(self):
        self.cameras = {}
        self.running = False
        
        # Inicializar Device Manager e QR Generator
        print("🔧 Inicializando sistema de identificação do dispositivo...")
        self.device_manager = DeviceManager()
        self.qr_generator = QRCodeGenerator(device_manager=self.device_manager)
        
        # Inicializar ONVIF Device Manager
        print("📡 Inicializando sistema ONVIF para câmeras...")
        self.onvif_manager = ONVIFDeviceManager()
        
        # Inicializar Supabase Manager
        print("☁️ Inicializando gerenciador do Supabase...")
        self.supabase_manager = SupabaseManager(device_manager=self.device_manager)
        
        # Replay Manager será inicializado após conexão com Supabase
        self.replay_manager = None
        
        # Inicializar Hierarchical Video Manager
        print("🎬 Inicializando gerenciador hierárquico de vídeos...")
        self.hierarchical_video_manager = HierarchicalVideoManager()
        
        # Obter Device ID único
        self.device_id = self.device_manager.get_device_id()
        print(f"🆔 Device ID do sistema: {self.device_id}")
        
        # Gerar QR Code do Device ID
        self._initialize_qr_code()
        
        # Carregar configurações
        self.load_config()
        
    def _initialize_qr_code(self):
        """Inicializa e gera o QR Code do Device ID"""
        try:
            print("🔳 Verificando QR Code do dispositivo...")
            
            # Verificar se já existe um QR code válido
            qr_status = self.qr_generator.verificar_qr_existente()
            
            if qr_status.get('exists') and qr_status.get('valid'):
                print(f"✅ QR Code existente encontrado:")
                print(f"   📱 PNG: {qr_status['png_file'].name}")
                print(f"   📄 Base64: {qr_status['base64_file'].name}")
            else:
                print("🔳 Gerando novo QR Code do Device ID...")
                qr_result = self.qr_generator.generate_device_qr_code()
                
                if 'error' not in qr_result:
                    print(f"✅ QR Code gerado com sucesso!")
                    print(f"   📱 PNG: {qr_result['files']['png_image']}")
                    print(f"   📄 Base64: {qr_result['files']['base64_file']}")
                    print(f"   📋 Info: {qr_result['files']['info_file']}")
                else:
                    print(f"❌ Erro ao gerar QR Code: {qr_result['error']}")
                    
        except Exception as e:
            print(f"❌ Erro na inicialização do QR Code: {e}")
            import traceback
            traceback.print_exc()
        
    def _initialize_replay_manager(self):
        """Inicializa o ReplayManager após conexão com Supabase"""
        try:
            if not self.replay_manager and self.supabase_manager.supabase:
                print("📊 Inicializando gerenciador de replays...")
                self.replay_manager = ReplayManager(supabase_manager=self.supabase_manager)
                log_success("ReplayManager inicializado com sucesso")
                return True
            elif self.replay_manager:
                log_debug("ReplayManager já inicializado")
                return True
            else:
                log_warning("Supabase não conectado - ReplayManager não inicializado")
                return False
        except Exception as e:
            log_error(f"Erro ao inicializar ReplayManager: {e}")
            return False
    
    def load_config(self):
        """Carrega as configurações do arquivo config.env"""
        # Busca o config.env na pasta pai (raiz do projeto)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(os.path.dirname(current_dir), "config.env")
        
        # Se não encontrar na pasta pai, tenta na pasta atual
        if not os.path.exists(config_path):
            config_path = os.path.join(current_dir, "config.env")
            
        if not os.path.exists(config_path):
            print(f"❌ Arquivo config.env não encontrado!")
            print(f"   Procurado em: {config_path}")
            print(f"   Certifique-se que o arquivo config.env está na raiz do projeto")
            return False
            
        print(f"📋 Carregando configurações de: {config_path}")
            
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    
                    # Ignora linhas vazias e comentários
                    if not line or line.startswith('#'):
                        continue
                    
                    # Verifica se a linha tem o formato correto
                    if '=' not in line:
                        print(f"⚠️  Linha {line_num} ignorada (formato inválido): {line}")
                        continue
                    
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Remove aspas se existirem
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    
                    if key.startswith('IP_CAMERA_'):
                        camera_id = key.replace('IP_CAMERA_', '').lower()
                        camera_name = f"Camera_{camera_id}"
                        
                        print(f"📹 Encontrada câmera: {camera_name} -> {value}")
                        
                        self.cameras[camera_name] = CameraRecorder(
                            camera_url=value,
                            camera_name=camera_name
                        )
                        
        except Exception as e:
            print(f"❌ Erro ao ler config.env: {e}")
            return False
        
        print(f"Carregadas {len(self.cameras)} câmeras:")
        for name in self.cameras.keys():
            print(f"  - {name}")
        
        return True
    
    def _sanitizar_nome_pasta(self, nome):
        """
        Sanitiza nomes de pastas removendo caracteres especiais e espaços
        
        Args:
            nome (str): Nome original da pasta
            
        Returns:
            str: Nome sanitizado com underscores no lugar de espaços
        """
        import re
        
        if not nome:
            return "pasta_sem_nome"
        
        # Converter para string se não for
        nome = str(nome)
        
        # Remover caracteres especiais e manter apenas letras, números, espaços e alguns símbolos
        nome_limpo = re.sub(r'[^\w\s\-_.]', '', nome)
        
        # Substituir múltiplos espaços por um único espaço
        nome_limpo = re.sub(r'\s+', ' ', nome_limpo)
        
        # Remover espaços no início e fim
        nome_limpo = nome_limpo.strip()
        
        # Substituir espaços por underscores
        nome_limpo = nome_limpo.replace(' ', '_')
        
        # Remover múltiplos underscores consecutivos
        nome_limpo = re.sub(r'_+', '_', nome_limpo)
        
        # Remover underscores no início e fim
        nome_limpo = nome_limpo.strip('_')
        
        # Se ficou vazio, usar nome padrão
        if not nome_limpo:
            nome_limpo = "pasta_sem_nome"
        
        return nome_limpo
    
    def get_device_id(self):
        """Retorna o Device ID único do sistema"""
        return self.device_id
    
    def get_device_info(self):
        """Retorna informações completas do dispositivo"""
        return self.device_manager.get_device_info()
    
    def regenerate_qr_code(self):
        """Regenera o QR Code do Device ID"""
        try:
            print("🔳 Regenerando QR Code do Device ID...")
            qr_result = self.qr_generator.generate_device_qr_code()
            
            if 'error' not in qr_result:
                print(f"✅ QR Code regenerado com sucesso!")
                print(f"   📱 PNG: {qr_result['files']['png_image']}")
                print(f"   📄 Base64: {qr_result['files']['base64_file']}")
                print(f"   📋 Info: {qr_result['files']['info_file']}")
                return qr_result
            else:
                print(f"❌ Erro ao regenerar QR Code: {qr_result['error']}")
                return None
                
        except Exception as e:
            print(f"❌ Erro na regeneração do QR Code: {e}")
            return None
    
    def list_qr_codes(self):
        """Lista todos os QR codes gerados"""
        return self.qr_generator.list_generated_qr_codes()
    
    def get_onvif_info(self, force_recreate=False):
        """Obtém informações ONVIF das câmeras"""
        try:
            print("📡 Obtendo informações ONVIF das câmeras...")
            return self.onvif_manager.obter_informacoes_cameras(force_recreate=force_recreate)
        except Exception as e:
            print(f"❌ Erro ao obter informações ONVIF: {e}")
            return None
    
    def scan_onvif_cameras(self):
        """Força um novo scan das câmeras ONVIF"""
        try:
            print("🔄 Executando novo scan ONVIF das câmeras...")
            return self.onvif_manager.obter_informacoes_cameras(force_recreate=True)
        except Exception as e:
            print(f"❌ Erro no scan ONVIF: {e}")
            return None
    
    def display_onvif_summary(self):
        """Exibe um resumo das informações ONVIF"""
        try:
            onvif_info = self.get_onvif_info()
            if not onvif_info:
                print("❌ Nenhuma informação ONVIF disponível")
                return
            
            print("\n📡 === RESUMO INFORMAÇÕES ONVIF ===")
            print("-" * 50)
            
            for camera_key, info in onvif_info.items():
                status = "✅ CONECTADA" if info['conexao']['status'] == 'conectado' else "❌ FALHA"
                device_id = info['dispositivo'].get('serial_number', 'N/A')
                device_uuid = info['dispositivo'].get('device_uuid', 'N/A')
                modelo = info['dispositivo'].get('modelo', 'N/A')
                fabricante = info['dispositivo'].get('fabricante', 'N/A')
                ip = info['configuracao'].get('ip', 'N/A')
                
                print(f"{camera_key.upper()}: {status}")
                print(f"   🌐 IP: {ip}")
                print(f"   🏭 Fabricante: {fabricante}")
                print(f"   📱 Modelo: {modelo}")
                print(f"   🔢 Serial: {device_id}")
                print(f"   🆔 UUID: {device_uuid}")
                print()
                
        except Exception as e:
            print(f"❌ Erro ao exibir resumo ONVIF: {e}")
    
    def create_save_path(self, camera_name):
        """Cria o caminho de salvamento com hierarquia de pastas"""
        now = datetime.now()
        
        # Caminho base na raiz do projeto (pasta pai da src)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        
        # Hierarquia: arena_central_da_leste/quadra_da_leeste/2025/07-July/28/10h/
        base_path = os.path.join(project_root, "arena_central_da_leste", "quadra_da_leeste")
        year = now.strftime("%Y")
        month = now.strftime("%m-%B")
        day = now.strftime("%d")
        hour = now.strftime("%Hh")
        
        # Nome do arquivo com timestamp
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        filename = f"{camera_name}_{timestamp}.mp4"
        
        full_path = os.path.join(base_path, year, month, day, hour, filename)
        return full_path
    
    def start_system(self):
        """Inicia o sistema de câmeras"""
        print("Iniciando sistema de câmeras...")
        
        # Iniciar todas as câmeras
        for camera in self.cameras.values():
            if not camera.start_capture():
                print(f"Falha ao iniciar {camera.camera_name}")
                return False
        
        self.running = True
        print("\n" + "="*50)
        print("SISTEMA DE GRAVAÇÃO ATIVO")
        print("Pressione 'S' para salvar os últimos 25 segundos")
        print("Pressione 'Q' para sair")
        print("="*50)
        
        return True
    
    def stop_system(self):
        """Para o sistema de câmeras"""
        print("\nParando sistema de câmeras...")
        self.running = False
        
        for camera in self.cameras.values():
            camera.stop_capture()
        
        print("Sistema parado.")
    
    def _capture_synchronized_buffer(self, camera, sync_timestamp):
        """Captura buffer sincronizado baseado no timestamp de referência"""
        try:
            with camera.buffer_lock:
                if len(camera.frame_buffer) == 0 or len(camera.timestamp_buffer) == 0:
                    return None
                
                frames = list(camera.frame_buffer)
                timestamps = list(camera.timestamp_buffer)
                
                # Encontrar o índice mais próximo do timestamp de sincronização
                # Queremos os frames ANTES do momento da tecla 'S'
                sync_index = len(timestamps) - 1  # Começar do final
                
                for i in range(len(timestamps) - 1, -1, -1):
                    if timestamps[i] <= sync_timestamp:
                        sync_index = i
                        break
                
                # Calcular quantos frames queremos (25 segundos)
                target_frames = camera.fps * camera.buffer_seconds
                
                # Determinar o índice de início
                start_index = max(0, sync_index - target_frames + 1)
                end_index = sync_index + 1
                
                # Extrair frames e timestamps sincronizados
                sync_frames = frames[start_index:end_index]
                sync_timestamps = timestamps[start_index:end_index]
                
                if len(sync_frames) > 0:
                    buffer_duration = sync_timestamps[-1] - sync_timestamps[0] if len(sync_timestamps) > 1 else 0
                    print(f"🎯 {camera.camera_name}: Sincronizado em {sync_timestamp:.3f}, {len(sync_frames)} frames, {buffer_duration:.1f}s")
                    
                    return {
                        'frames': sync_frames,
                        'timestamps': sync_timestamps,
                        'sync_timestamp': sync_timestamp,
                        'camera_name': camera.camera_name
                    }
                
                return None
                
        except Exception as e:
            print(f"❌ Erro na sincronização do buffer {camera.camera_name}: {e}")
            return None

    def _save_synchronized_buffer(self, camera, sync_buffer, output_path):
        """Salva buffer sincronizado em formato otimizado"""
        print(f"🎬 [{camera.camera_name}] Iniciando salvamento sincronizado...")
        
        # Marcar que está salvando
        camera.saving = True
        print(f"🔒 [{camera.camera_name}] Flag saving ativada")
        
        try:
            frames = sync_buffer['frames']
            timestamps = sync_buffer['timestamps']
            
            # Verificar se há frames suficientes
            min_frames = camera.fps * 5  # Pelo menos 5 segundos
            if len(frames) < min_frames:
                print(f"❌ [{camera.camera_name}] Buffer insuficiente: {len(frames)} frames (mínimo: {min_frames})")
                return False
            
            # Calcular tempo real do buffer
            if len(timestamps) > 1:
                buffer_duration = timestamps[-1] - timestamps[0]
                real_fps = len(frames) / buffer_duration if buffer_duration > 0 else 0
                print(f"📊 [{camera.camera_name}] {len(frames)} frames, {buffer_duration:.1f}s, FPS real: {real_fps:.1f}")
            
            # Criar pasta se não existir
            print(f"📁 [{camera.camera_name}] Criando diretório: {os.path.dirname(output_path)}")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Configurar codec otimizado
            print(f"🎥 [{camera.camera_name}] Configurando VideoWriter otimizado...")
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            print(f"📐 [{camera.camera_name}] Resolução: {camera.frame_width}x{camera.frame_height}")
            
            # Usar FPS reduzido para arquivo menor
            optimized_fps = int(os.getenv('VIDEO_FPS_UPLOAD', '15'))
            
            # Criar arquivo temporário primeiro
            temp_output = output_path.replace('.mp4', '_temp.mp4')
            out = cv2.VideoWriter(temp_output, fourcc, optimized_fps, 
                                 (camera.frame_width, camera.frame_height))
            
            if not out.isOpened():
                print(f"❌ [{camera.camera_name}] Erro ao criar VideoWriter")
                return False
            
            print(f"✅ [{camera.camera_name}] VideoWriter otimizado configurado (MP4V, {optimized_fps} FPS)")
            
            # Calcular step para manter FPS desejado
            frame_step = max(1, int(camera.fps / optimized_fps))
            print(f"💾 [{camera.camera_name}] Salvando frames sincronizados (step: {frame_step})...")
            
            frames_written = 0
            start_time = time.time()
            
            for i in range(0, len(frames), frame_step):
                try:
                    frame = frames[i]
                    if frame is not None:
                        # Verificar timeout (máximo 2 minutos)
                        elapsed = time.time() - start_time
                        if elapsed > 120:  # 2 minutos
                            print(f"⏰ [{camera.camera_name}] Timeout após {elapsed:.1f}s - salvando {frames_written} frames")
                            break
                        
                        # Aplicar marca d'água se habilitada
                        if camera.watermark_manager:
                            frame = camera.watermark_manager.apply_watermark(frame)
                        
                        out.write(frame)
                        frames_written += 1
                        
                        # Progress report a cada 50 frames salvos
                        if frames_written % 50 == 0:
                            progress = (i / len(frames)) * 100
                            elapsed = time.time() - start_time
                            fps_write = frames_written / elapsed if elapsed > 0 else 0
                            watermark_status = "com marca d'água" if camera.watermark_manager else "sem marca d'água"
                            print(f"📈 [{camera.camera_name}] Progresso: {frames_written} frames salvos ({progress:.1f}%) - {fps_write:.1f} fps escrita ({watermark_status})")
                    else:
                        print(f"⚠️  [{camera.camera_name}] Frame {i} é None")
                        
                except Exception as e:
                    print(f"❌ [{camera.camera_name}] Erro ao escrever frame {i}: {e}")
                    continue
            
            print(f"🔚 [{camera.camera_name}] Finalizando arquivo temporário...")
            total_time = time.time() - start_time
            print(f"⏱️  [{camera.camera_name}] Tempo total de escrita: {total_time:.1f}s")
            
            out.release()
            
            # Verificar se o arquivo temporário foi criado
            if os.path.exists(temp_output):
                temp_size = os.path.getsize(temp_output) / (1024*1024)
                print(f"📏 [{camera.camera_name}] Arquivo temporário: {temp_size:.1f} MB, Frames: {frames_written}")
                
                # Comprimir para upload se habilitado
                compression_enabled = os.getenv('VIDEO_COMPRESSION_ENABLED', 'true').lower() == 'true'
                if compression_enabled:
                    compressed_path = camera.compress_video_for_upload(temp_output, output_path)
                    
                    # Remover arquivo temporário
                    if os.path.exists(temp_output):
                        os.remove(temp_output)
                    
                    if compressed_path and os.path.exists(compressed_path):
                        final_size = os.path.getsize(compressed_path) / (1024*1024)
                        print(f"✅ [{camera.camera_name}] Arquivo final comprimido: {final_size:.1f} MB")
                        return True
                    else:
                        print(f"❌ [{camera.camera_name}] Falha na compressão")
                        return False
                else:
                    # Apenas renomear arquivo temporário
                    os.rename(temp_output, output_path)
                    print(f"✅ [{camera.camera_name}] Arquivo salvo sem compressão: {temp_size:.1f} MB")
                    return True
            else:
                print(f"❌ [{camera.camera_name}] Arquivo temporário não foi criado")
                return False
            
        except Exception as e:
            print(f"❌ [{camera.camera_name}] Erro durante salvamento sincronizado: {e}")
            import traceback
            traceback.print_exc()
            return False
            
        finally:
            # Reativar verificações após salvamento
            camera.saving = False
            print(f"🔓 [{camera.camera_name}] Flag saving desativada")

    def save_all_cameras(self):
        """Salva os últimos 25 segundos de todas as câmeras e faz upload para Supabase"""
        print("\n📹 SALVANDO E ENVIANDO ÚLTIMOS 25 SEGUNDOS...")
        
        # Verificar se o ReplayManager está inicializado
        if self.replay_manager is None:
            print("⚠️ ReplayManager não inicializado. Tentando inicializar...")
            self._initialize_replay_manager()
            if self.replay_manager is None:
                print("❌ Falha na inicialização do ReplayManager. Continuando sem registro de replays.")
        
        # SINCRONIZAÇÃO CRÍTICA: Capturar timestamp exato no momento da tecla 'S'
        sync_timestamp = time.time()
        now = datetime.now()
        # Converter timestamp da tecla 'S' para UTC para uso no banco de dados
        key_press_timestamp_utc = datetime.fromtimestamp(sync_timestamp, tz=timezone.utc)
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        
        print(f"🕐 Timestamp de sincronização: {sync_timestamp:.3f}")
        print(f"📅 Horário de referência: {now.strftime('%H:%M:%S.%f')[:-3]}")
        print(f"⏰ Timestamp da tecla 'S' (UTC): {key_press_timestamp_utc.strftime('%H:%M:%S.%f')[:-3]}")
        
        # ETAPA 0: Sincronizar buffers de todas as câmeras SIMULTANEAMENTE
        print("🔄 Sincronizando buffers de todas as câmeras...")
        synchronized_buffers = {}
        
        for camera_name, camera in self.cameras.items():
            try:
                # Capturar buffer com timestamp de referência
                sync_buffer = self._capture_synchronized_buffer(camera, sync_timestamp)
                if sync_buffer:
                    synchronized_buffers[camera_name] = sync_buffer
                    print(f"✅ {camera_name}: Buffer sincronizado ({len(sync_buffer['frames'])} frames)")
                else:
                    print(f"❌ {camera_name}: Falha na sincronização do buffer")
            except Exception as e:
                print(f"❌ {camera_name}: Erro na sincronização - {e}")
        
        if not synchronized_buffers:
            print("❌ Nenhuma câmera foi sincronizada. Abortando salvamento.")
            return
        
        saved_files = []
        failed_cameras = []
        upload_results = []
        
        # ETAPA 1: Validação OBRIGATÓRIA de arena/quadra
        arena_nome = None
        quadra_nome = None
        upload_enabled = False
        
        try:
            # Buscar nomes reais da arena/quadra
            names_result = self.supabase_manager.get_arena_quadra_names()
            
            if names_result['success']:
                # Sanitizar nomes para remover espaços e caracteres especiais
                arena_nome = self._sanitizar_nome_pasta(names_result['arena_nome'])
                quadra_nome = self._sanitizar_nome_pasta(names_result['quadra_nome'])
                upload_enabled = True
                print(f"✅ Associação validada: {arena_nome} / {quadra_nome}")
            else:
                # VALIDAÇÃO OBRIGATÓRIA: Não salvar se não há arena/quadra válida
                print(f"❌ SALVAMENTO BLOQUEADO: Dispositivo não associado a arena/quadra válida")
                print(f"📋 Motivo: {names_result.get('message', 'Associação não encontrada')}")
                print(f"🚫 Nenhum vídeo será salvo até que o dispositivo seja associado corretamente")
                return
                
        except Exception as e:
            # VALIDAÇÃO OBRIGATÓRIA: Não salvar em caso de erro na validação
            print(f"❌ SALVAMENTO BLOQUEADO: Erro na validação da hierarquia")
            print(f"📋 Erro: {e}")
            print(f"🚫 Nenhum vídeo será salvo até que a conexão seja restabelecida")
            return
        
        # ETAPA 2: Salvamento e upload por câmera usando buffers sincronizados
        # OTIMIZAÇÃO: Processar câmeras em paralelo para melhor performance
        print(f"🚀 Iniciando processamento paralelo de {len(synchronized_buffers)} câmeras...")
        
        import concurrent.futures
        
        def process_camera_sync(camera_name_and_buffer):
            camera_name, sync_buffer = camera_name_and_buffer
            camera = self.cameras[camera_name]
            
            try:
                # Criar caminho com nomes reais
                base_path = self.create_save_path_with_names(camera.camera_name, timestamp, arena_nome, quadra_nome)
                output_path = base_path.replace('.mp4', '_WEB.mp4')
                
                print(f"📁 [{camera_name}] Salvando localmente: {arena_nome}/{quadra_nome}/{now.strftime('%Y/%m-%B/%d/%Hh')}/")
                
                # Salvamento local usando buffer sincronizado
                save_start_time = time.time()
                if self._save_synchronized_buffer(camera, sync_buffer, output_path):
                    save_duration = time.time() - save_start_time
                    file_size = os.path.getsize(output_path) / (1024*1024) if os.path.exists(output_path) else 0
                    
                    return {
                        'camera_name': camera_name,
                        'success': True,
                        'output_path': output_path,
                        'file_size': file_size,
                        'save_duration': save_duration
                    }
                else:
                    return {
                        'camera_name': camera_name,
                        'success': False,
                        'error': 'Falha no salvamento local'
                    }
                    
            except Exception as e:
                return {
                    'camera_name': camera_name,
                    'success': False,
                    'error': str(e)
                }
        
        # Executar processamento paralelo
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(synchronized_buffers)) as executor:
            # Submeter todas as tarefas
            future_to_camera = {
                executor.submit(process_camera_sync, item): item[0] 
                for item in synchronized_buffers.items()
            }
            
            # Coletar resultados conforme completam
            save_results = []
            for future in concurrent.futures.as_completed(future_to_camera):
                camera_name = future_to_camera[future]
                try:
                    result = future.result()
                    save_results.append(result)
                    
                    if result['success']:
                        print(f"✅ {result['camera_name']}: Salvamento concluído ({result['file_size']:.1f} MB em {result['save_duration']:.1f}s)")
                        saved_files.append(result['output_path'])
                    else:
                        print(f"❌ {result['camera_name']}: {result['error']}")
                        failed_cameras.append(result['camera_name'])
                        
                except Exception as exc:
                    print(f"❌ {camera_name}: Exceção durante processamento - {exc}")
                    failed_cameras.append(camera_name)
        
        print(f"🏁 Processamento paralelo concluído: {len(saved_files)} sucessos, {len(failed_cameras)} falhas")
        
        # ETAPA 3: Upload sequencial (para evitar sobrecarga do Supabase)
        if saved_files and upload_enabled:
            print(f"\n☁️ Iniciando uploads sequenciais para {len(saved_files)} arquivos...")
            
            for i, output_path in enumerate(saved_files):
                # Encontrar o resultado correspondente
                camera_result = next((r for r in save_results if r.get('output_path') == output_path), None)
                if not camera_result:
                    continue
                    
                camera_name = camera_result['camera_name']
                camera = self.cameras[camera_name]
                file_size = camera_result['file_size']
                
                print(f"\n☁️ Upload {i+1}/{len(saved_files)}: {camera_name}")
                
                try:
                    # Criar caminho no bucket
                    bucket_path = self.create_bucket_path(camera.camera_name, timestamp, arena_nome, quadra_nome)
                    
                    # Upload
                    upload_start = time.time()
                    upload_result = self.supabase_manager.upload_video_to_bucket(
                        output_path, 
                        bucket_path,
                        timeout_seconds=int(os.getenv('UPLOAD_TIMEOUT_SECONDS', '300'))
                    )
                    
                    if upload_result['success']:
                        upload_time = upload_result['upload_time']
                        print(f"   ✅ Upload concluído em {upload_time:.1f}s")
                        
                        # Verificação imediata
                        verify_result = self.supabase_manager.verify_upload_success(
                            bucket_path, 
                            expected_size=int(file_size * 1024 * 1024)
                        )
                        
                        if verify_result['success']:
                            print(f"   ✅ Verificação bem-sucedida")
                            
                            # Registro replay (mantendo lógica existente)
                            try:
                                if self.replay_manager is None:
                                    print(f"   ⚠️ ReplayManager não disponível, pulando registro")
                                else:
                                    camera_uuid = self._get_camera_uuid_from_name(camera_name)
                                    public_url = upload_result.get('public_url', '')
                                    
                                    if not self._validar_url_completa(public_url):
                                        print(f"   🔄 Gerando URL assinada...")
                                        try:
                                            signed_url = self.hierarchical_video_manager._obter_url_assinada(bucket_path)
                                            if signed_url and self._validar_url_completa(signed_url):
                                                public_url = signed_url
                                                print(f"   ✅ URL assinada gerada")
                                            else:
                                                print(f"   ❌ Falha na URL assinada")
                                                continue
                                        except Exception as url_error:
                                            print(f"   ❌ Erro na URL assinada: {url_error}")
                                            continue
                                    
                                    if self._validar_url_completa(public_url):
                                        replay_result = self.replay_manager.insert_replay_record(
                                            camera_id=camera_uuid,
                                            video_url=public_url,
                                            timestamp_video=key_press_timestamp_utc,
                                            bucket_path=bucket_path
                                        )
                                        
                                        if replay_result['success']:
                                            print(f"   📊 Registro replay inserido")
                                        else:
                                            print(f"   ❌ Erro no registro replay: {replay_result.get('error', 'Erro desconhecido')}")
                                            
                            except Exception as replay_error:
                                print(f"   ❌ Erro no registro replay: {replay_error}")
                            
                            # Exclusão do arquivo local
                            if self._excluir_arquivo_local_apos_upload(output_path, camera_name):
                                print(f"   🗑️ Arquivo local removido")
                                
                            upload_results.append({
                                'camera': camera_name,
                                'success': True,
                                'local_path': output_path,
                                'bucket_path': bucket_path,
                                'upload_time': upload_time,
                                'file_size': file_size,
                                'local_file_deleted': True
                            })
                        else:
                            print(f"   ⚠️ Verificação falhou: {verify_result['message']}")
                            upload_results.append({
                                'camera': camera_name,
                                'success': False,
                                'error': verify_result['message'],
                                'local_file_deleted': False
                            })
                    else:
                        print(f"   ❌ Upload falhou: {upload_result['message']}")
                        upload_results.append({
                            'camera': camera_name,
                            'success': False,
                            'error': upload_result['message'],
                            'local_file_deleted': False
                        })
                        
                except Exception as upload_error:
                    print(f"   ❌ Erro no upload: {upload_error}")
                    upload_results.append({
                        'camera': camera_name,
                        'success': False,
                        'error': str(upload_error),
                        'local_file_deleted': False
                    })
        else:
            # Sem upload - criar resultados vazios
            upload_results = []
            for result in save_results:
                if result['success']:
                    upload_results.append({
                        'camera': result['camera_name'],
                        'success': False,
                        'error': 'Upload não autorizado - dispositivo não associado',
                        'local_file_deleted': False
                    })
        
        # ETAPA 3: Relatório final consolidado
        print(f"\n📊 RELATÓRIO FINAL:")
        
        if saved_files:
            successful_uploads = [r for r in upload_results if r['success']]
            failed_uploads = [r for r in upload_results if not r['success']]
            deleted_files = [r for r in upload_results if r.get('local_file_deleted', False)]
            
            if upload_enabled:
                print(f"📊 Status: {len(saved_files)}/{len(self.cameras)} vídeos salvos localmente e {len(successful_uploads)}/{len(saved_files)} enviados para bucket")
                print(f"🗑️ Limpeza: {len(deleted_files)}/{len(successful_uploads)} arquivos locais excluídos automaticamente")
                
                if successful_uploads:
                    total_upload_time = sum(r.get('upload_time', 0) for r in successful_uploads)
                    total_size = sum(r.get('file_size', 0) for r in successful_uploads)
                    print(f"⏱️ Tempo total de upload: {total_upload_time:.1f}s")
                    print(f"📦 Tamanho total enviado: {total_size:.1f} MB")
                    print(f"💾 Espaço local liberado: {total_size:.1f} MB")
                
                if failed_uploads:
                    print(f"❌ Falhas no upload (arquivos mantidos localmente):")
                    for result in failed_uploads:
                        print(f"   • {result['camera']}: {result['error']}")
            else:
                print(f"📊 Status: {len(saved_files)}/{len(self.cameras)} localmente - Upload não autorizado")
                print(f"🗑️ Limpeza: 0 arquivos excluídos (upload desabilitado)")
        
        if failed_cameras:
            print(f"\n❌ Falha ao salvar câmeras: {', '.join(failed_cameras)}")
            
        if not saved_files and not failed_cameras:
            print("❌ Nenhum arquivo foi salvo.")

    def create_save_path_with_names(self, camera_name, timestamp, arena_nome, quadra_nome):
        """Cria o caminho de salvamento com nomes reais da arena/quadra"""
        now = datetime.now()
        
        # Caminho base na raiz do projeto
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        
        # Hierarquia com nomes reais
        base_path = os.path.join(project_root, arena_nome, quadra_nome)
        year = now.strftime("%Y")
        month = now.strftime("%m-%B")
        day = now.strftime("%d")
        hour = now.strftime("%Hh")
        
        # Nome do arquivo com timestamp fornecido
        filename = f"{camera_name}_{timestamp}.mp4"
        
        full_path = os.path.join(base_path, year, month, day, hour, filename)
        return full_path

    def create_bucket_path(self, camera_name, timestamp, arena_nome, quadra_nome):
        """Cria o caminho no bucket com estrutura hierárquica"""
        now = datetime.now()
        
        # Estrutura hierárquica no bucket
        year = now.strftime("%Y")
        month = now.strftime("%m-%B")
        day = now.strftime("%d")
        hour = now.strftime("%Hh")
        
        # Nome do arquivo
        filename = f"{camera_name}_{timestamp}_WEB.mp4"
        
        # Caminho completo no bucket
        bucket_path = f"{arena_nome}/{quadra_nome}/{year}/{month}/{day}/{hour}/{filename}"
        return bucket_path
    
    def create_save_path_with_timestamp(self, camera_name, timestamp):
        """Cria o caminho de salvamento com timestamp específico"""
        now = datetime.now()
        
        # Caminho base na raiz do projeto (pasta pai da src)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        
        # Hierarquia: arena_central_da_leste/quadra_da_leeste/2025/07-July/28/10h/
        base_path = os.path.join(project_root, "arena_central_da_leste", "quadra_da_leeste")
        year = now.strftime("%Y")
        month = now.strftime("%m-%B")
        day = now.strftime("%d")
        hour = now.strftime("%Hh")
        
        # Nome do arquivo com timestamp fornecido
        filename = f"{camera_name}_{timestamp}.mp4"
        
        full_path = os.path.join(base_path, year, month, day, hour, filename)
        return full_path
    
    def run(self):
        """Executa o loop principal do sistema"""
        # Sistema já foi iniciado na main(), apenas executar o loop
        try:
            while self.running:
                # Exibir frames de todas as câmeras
                for name, camera in self.cameras.items():
                    frame = camera.get_latest_frame()
                    if frame is not None:
                        # Redimensionar para exibição (opcional)
                        display_frame = cv2.resize(frame, (960, 540))
                        cv2.imshow(name, display_frame)

                # Verificar teclas pressionadas (1ms de delay)
                key = cv2.waitKey(1) & 0xFF

                if key == ord('s'):
                    self.save_all_cameras()
                
                elif key == ord('q'):
                    break

                # Fechar janelas se o 'X' for clicado
                if len(self.cameras) > 0:
                    first_camera = list(self.cameras.keys())[0]
                    if cv2.getWindowProperty(first_camera, cv2.WND_PROP_VISIBLE) < 1:
                        break

        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário")
        
        finally:
            self.stop_system()
            cv2.destroyAllWindows()
    
    def _display_device_info(self):
        """Exibe informações do Device ID e QR Code"""
        print("\n" + "="*60)
        print("📋 INFORMAÇÕES DO DISPOSITIVO")
        print("="*60)
        print(f"🆔 Device ID: {self.device_id}")
        
        device_info = self.get_device_info()
        if device_info and 'error' not in device_info:
            print(f"💻 Sistema: {device_info.get('system', 'N/A')}")
            print(f"🏠 Hostname: {device_info.get('hostname', 'N/A')}")
            print(f"🏗️  Arquitetura: {device_info.get('machine', 'N/A')}")
            print(f"📅 Criado em: {device_info.get('created_at', 'N/A')}")
            
            # Verificar integridade
            integrity_ok = self.device_manager.verify_device_integrity()
            print(f"🔒 Integridade: {'✅ OK' if integrity_ok else '❌ FALHA'}")
        
        # Listar QR codes disponíveis
        qr_files = self.list_qr_codes()
        if qr_files['png_images']:
            print(f"\n🔳 QR Codes disponíveis: {len(qr_files['png_images'])} imagens")
            for png_file in qr_files['png_images']:
                file_size = png_file.stat().st_size / 1024  # KB
                print(f"   📱 {png_file.name} ({file_size:.1f} KB)")
        else:
            print("\n🔳 Nenhum QR Code encontrado")
        
        print("="*60)
        print("Pressione qualquer tecla para continuar...")
        print("="*60)

    def _get_camera_uuid_from_name(self, camera_name):
        """
        Obtém UUID da câmera baseado no nome (Camera_1, Camera_2)
        Usa dados ONVIF cadastrados na tabela cameras
        
        Args:
            camera_name (str): Nome da câmera (ex: "Camera_1")
            
        Returns:
            str: UUID da câmera
        """
        try:
            # Tentar obter informações ONVIF primeiro
            onvif_info = self.get_onvif_info()
            
            if onvif_info:
                # Mapear nome da câmera para chave ONVIF (Camera_1 -> camera_1)
                camera_key = camera_name.lower()  # Camera_1 -> camera_1
                
                log_debug(f"Buscando UUID ONVIF para {camera_name} (chave: {camera_key})")
                
                if camera_key in onvif_info:
                    device_info = onvif_info[camera_key].get('dispositivo', {})
                    device_uuid = device_info.get('device_uuid')
                    
                    if device_uuid and device_uuid != 'N/A':
                        log_debug(f"UUID ONVIF encontrado para {camera_name}: {device_uuid}")
                        return device_uuid
                    else:
                        log_warning(f"UUID ONVIF inválido para {camera_name}: {device_uuid}")
                else:
                    log_warning(f"Chave {camera_key} não encontrada no ONVIF. Chaves disponíveis: {list(onvif_info.keys())}")
            
            # Fallback: Buscar diretamente na tabela cameras do Supabase
            log_info(f"Tentando buscar UUID na tabela cameras para {camera_name}")
            
            if self.supabase_manager and self.supabase_manager.supabase:
                try:
                    # Extrair número da câmera (Camera_1 -> 1)
                    camera_number = camera_name.split('_')[-1] if '_' in camera_name else '1'
                    
                    # Buscar câmera por ordem na tabela
                    response = self.supabase_manager.supabase.table('cameras').select('id, nome, ordem').eq('ordem', int(camera_number)).execute()
                    
                    if response.data:
                        camera_data = response.data[0]
                        camera_uuid = camera_data['id']
                        log_success(f"UUID encontrado na tabela cameras para {camera_name}: {camera_uuid}")
                        return camera_uuid
                    else:
                        log_warning(f"Câmera com ordem {camera_number} não encontrada na tabela")
                        
                except Exception as db_error:
                    log_error(f"Erro ao buscar na tabela cameras: {db_error}")
            
            # Fallback final: gerar UUID determinístico (mas alertar que não será encontrado)
            import hashlib
            import uuid
            
            # Criar string única combinando device_id e nome da câmera
            unique_string = f"{self.device_id}_{camera_name}"
            
            # Gerar hash MD5 da string
            hash_object = hashlib.md5(unique_string.encode())
            hash_hex = hash_object.hexdigest()
            
            # Converter hash para UUID válido
            camera_uuid = str(uuid.UUID(hash_hex))
            
            log_warning(f"⚠️ UUID determinístico gerado para {camera_name}: {camera_uuid}")
            log_warning(f"⚠️ Este UUID pode não existir na tabela cameras - registro replay pode falhar")
            return camera_uuid
            
        except Exception as e:
            log_error(f"Erro ao obter UUID da câmera {camera_name}: {e}")
            
            # Fallback final: UUID baseado apenas no nome
            import hashlib
            import uuid
            
            hash_object = hashlib.md5(camera_name.encode())
            hash_hex = hash_object.hexdigest()
            camera_uuid = str(uuid.UUID(hash_hex))
            
            log_error(f"❌ UUID de emergência gerado para {camera_name}: {camera_uuid}")
            log_error(f"❌ Este UUID definitivamente não existe na tabela - registro replay falhará")
            return camera_uuid

    def _excluir_arquivo_local_apos_upload(self, file_path, camera_name):
        """
        Exclui o arquivo de vídeo local após upload bem-sucedido.
        
        Args:
            file_path (str): Caminho completo do arquivo local
            camera_name (str): Nome da câmera para logs
            
        Returns:
            bool: True se a exclusão foi bem-sucedida
        """
        try:
            if os.path.exists(file_path):
                # Obter tamanho do arquivo antes da exclusão para logs
                file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
                
                # Excluir o arquivo
                os.remove(file_path)
                
                print(f" → 🗑️ Arquivo local excluído ({file_size:.1f} MB liberados)")
                log_info(f"Arquivo local excluído após upload: {file_path}")
                
                # Verificar se a pasta ficou vazia e remover se necessário
                self._limpar_pastas_vazias(os.path.dirname(file_path))
                
                return True
            else:
                log_warning(f"Arquivo não encontrado para exclusão: {file_path}")
                return False
                
        except Exception as e:
            print(f" → ❌ Erro ao excluir arquivo local: {e}")
            log_error(f"Erro ao excluir arquivo local {file_path}: {e}")
            return False

    def _limpar_pastas_vazias(self, dir_path):
        """
        Remove pastas vazias recursivamente, mantendo a estrutura base.
        
        Args:
            dir_path (str): Caminho do diretório para verificar
        """
        try:
            # Não remover a pasta raiz do projeto
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(current_dir)
            
            # Não remover pastas muito próximas da raiz
            if dir_path == project_root or len(os.path.relpath(dir_path, project_root).split(os.sep)) < 3:
                return
            
            # Verificar se a pasta está vazia
            if os.path.exists(dir_path) and os.path.isdir(dir_path):
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    log_debug(f"Pasta vazia removida: {dir_path}")
                    
                    # Verificar pasta pai recursivamente
                    parent_dir = os.path.dirname(dir_path)
                    self._limpar_pastas_vazias(parent_dir)
                    
        except Exception as e:
            log_debug(f"Erro ao limpar pasta vazia {dir_path}: {e}")

    def _validar_url_completa(self, url):
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

def main():
    """Função principal"""
    # Limpar cache de logs para nova execução
    system_logger.clear_cache()
    
    log_info("🔧 Inicializando Sistema de Câmeras...")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    try:
        # Criar sistema
        system = CameraSystem()
        
        # 1. Device Manager
        device_id = system.get_device_id()
        if device_id:
            log_success(f"✅ Device Manager ({device_id[:8]}...)")
        else:
            log_error("❌ Device Manager - Falha ao obter Device ID")
            return
        
        # 2. QR Code Generator
        qr_files = system.list_qr_codes()
        if qr_files['png_images']:
            log_success(f"✅ QR Code Generator ({len(qr_files['png_images'])} códigos)")
        else:
            log_warning("⚠️ QR Code Generator - Nenhum código encontrado")
        
        # 3. ONVIF Integration
        onvif_info = system.get_onvif_info()
        if onvif_info:
            camera_count = len([info for info in onvif_info.values() if 'error' not in info])
            if camera_count > 0:
                log_success(f"✅ ONVIF Integration ({camera_count} câmeras)")
            else:
                log_warning("⚠️ ONVIF Integration - Câmeras com erro")
        else:
            log_warning("⚠️ ONVIF Integration - Nenhuma câmera encontrada")
        
        # 4. Supabase Connection
        try:
            # Conectar sem logs verbosos
            if system.supabase_manager.conectar_supabase():
                log_success("✅ Supabase Connection")
                
                # Conectar o hierarchical_video_manager ao Supabase também
                if system.hierarchical_video_manager.conectar_supabase():
                    log_success("✅ Hierarchical Video Manager Supabase Connection")
                else:
                    log_warning("⚠️ Hierarchical Video Manager - Falha na conexão Supabase")
                
                # Inicializar ReplayManager após conexão
                system._initialize_replay_manager()
                
                # Execução automática do Supabase (sem logs duplicados)
                resultado = system.supabase_manager.executar_verificacao_completa()
                
                if resultado['success']:
                    log_success("✅ Supabase Integration (totem e câmeras)")
                else:
                    log_warning(f"⚠️ Supabase Integration - {resultado['message']}")
            else:
                log_error("❌ Supabase Connection - Falha na conexão")
        except Exception as e:
            log_error(f"❌ Supabase Connection - Erro: {e}")
        
        # 5. Camera Buffers
        if system.start_system():
            camera_count = len(system.cameras)
            log_success(f"✅ Camera Buffers ({camera_count} câmeras, 25s cada)")
        else:
            log_error("❌ Camera Buffers - Falha na inicialização")
            return
        
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_success("🎉 SISTEMA PRONTO!")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("💡 Controles:")
        print("   • Pressione 'S' para salvar os últimos 25 segundos")
        print("   • Pressione 'Q' para sair")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # Executar sistema
        system.run()
        
    except Exception as e:
        log_error(f"Erro crítico na inicialização: {e}")
        return

if __name__ == "__main__":
    main()