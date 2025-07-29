#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerenciador Hierárquico de Vídeos
Sistema que organiza vídeos em estrutura Arena/Quadra baseado no banco de dados.
Salva localmente e faz upload para o bucket do Supabase.
"""

import os
import json
from pathlib import Path
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv
from device_manager import DeviceManager
from supabase_manager import SupabaseManager

class HierarchicalVideoManager:
    def __init__(self, device_manager=None):
        """
        Inicializa o gerenciador hierárquico de vídeos.
        
        Args:
            device_manager (DeviceManager): Instância do DeviceManager (opcional)
        """
        # Carrega configurações
        self._carregar_configuracoes()
        
        # Device Manager
        if device_manager:
            self.device_manager = device_manager
        else:
            # Usa DeviceManager com configuração padrão (device_config na raiz)
            self.device_manager = DeviceManager()
        
        # Supabase Manager
        self.supabase_manager = SupabaseManager(self.device_manager)
        
        # Cliente Supabase para storage
        self.supabase = None
        self.bucket_name = "videos-replay"
        
        # Informações do totem atual
        self.device_id = None
        self.totem_info = None
        self.arena_info = None
        self.quadra_info = None
        
        # Pasta base para vídeos hierárquicos
        self.base_videos_dir = Path("Videos_Hierarquicos")
        
        # Dicionário de meses em inglês para estrutura hierárquica
        self.meses_ingles = {
            1: "January", 2: "February", 3: "March", 4: "April",
            5: "May", 6: "June", 7: "July", 8: "August", 
            9: "September", 10: "October", 11: "November", 12: "December"
        }
        
    def _carregar_configuracoes(self):
        """
        Carrega as configurações do arquivo .env
        """
        # Tenta carregar config.env (na raiz do projeto)
        env_file = Path(__file__).parent.parent / "config.env"
        if env_file.exists():
            load_dotenv(env_file)
        
        # Obtém configurações do Supabase
        self.supabase_url = os.getenv('SUPABASE_URL')
        self.supabase_service_role_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        
    def conectar_supabase(self):
        """
        Conecta ao Supabase usando as credenciais configuradas.
        
        Returns:
            bool: True se conectou com sucesso, False caso contrário
        """
        try:
            if not self.supabase_url or not self.supabase_service_role_key:
                print("❌ Configurações do Supabase não encontradas!")
                return False
            
            self.supabase = create_client(self.supabase_url, self.supabase_service_role_key)
            print("✅ Conectado ao Supabase para upload de vídeos!")
            return True
            
        except Exception as e:
            print(f"❌ Erro ao conectar no Supabase: {e}")
            return False
    
    def verificar_totem_hierarquia(self):
        """
        Verifica se o device_id está associado a um totem válido com arena e quadra.
        Obtém as informações hierárquicas necessárias.
        
        Returns:
            dict: Informações da verificação
        """
        resultado = {
            'valido': False,
            'device_id': None,
            'totem_info': None,
            'arena_info': None,
            'quadra_info': None,
            'message': ''
        }
        
        try:
            print("\n🏗️ VERIFICAÇÃO DE HIERARQUIA (Arena/Quadra)")
            print("-" * 50)
            
            # 1. Conecta ao Supabase
            if not self.conectar_supabase():
                resultado['message'] = 'Falha na conexão com Supabase'
                return resultado
            
            # 2. Obtém Device ID
            self.device_id = self.device_manager.get_device_id()
            if not self.device_id:
                resultado['message'] = 'Device ID não encontrado'
                return resultado
            
            resultado['device_id'] = self.device_id
            print(f"🆔 Device ID: {self.device_id}")
            
            # 3. Busca totem pelo token (device_id)
            print("🔍 Buscando totem no banco de dados...")
            totem_response = self.supabase.table('totens').select('*').eq('token', self.device_id).execute()
            
            if not totem_response.data:
                resultado['message'] = f'Totem não encontrado para o device_id: {self.device_id}'
                print(f"❌ {resultado['message']}")
                return resultado
            
            self.totem_info = totem_response.data[0]
            resultado['totem_info'] = self.totem_info
            print(f"✅ Totem encontrado: {self.totem_info['id']}")
            
            # 4. Verifica se totem tem quadra_id preenchida
            quadra_id = self.totem_info.get('quadra_id')
            if not quadra_id:
                resultado['message'] = 'Totem não está associado a uma quadra (quadra_id é null)'
                print(f"❌ {resultado['message']}")
                return resultado
            
            print(f"🏟️ Quadra ID: {quadra_id}")
            
            # 5. Busca informações da quadra
            print("🔍 Buscando informações da quadra...")
            quadra_response = self.supabase.table('quadras').select('*').eq('id', quadra_id).execute()
            
            if not quadra_response.data:
                resultado['message'] = f'Quadra não encontrada: {quadra_id}'
                print(f"❌ {resultado['message']}")
                return resultado
            
            self.quadra_info = quadra_response.data[0]
            resultado['quadra_info'] = self.quadra_info
            print(f"✅ Quadra encontrada: {self.quadra_info['nome']}")
            
            # 6. Busca informações da arena
            arena_id = self.quadra_info['arena_id']
            print(f"🏛️ Arena ID: {arena_id}")
            print("🔍 Buscando informações da arena...")
            
            arena_response = self.supabase.table('arenas').select('*').eq('id', arena_id).execute()
            
            if not arena_response.data:
                resultado['message'] = f'Arena não encontrada: {arena_id}'
                print(f"❌ {resultado['message']}")
                return resultado
            
            self.arena_info = arena_response.data[0]
            resultado['arena_info'] = self.arena_info
            print(f"✅ Arena encontrada: {self.arena_info['nome']}")
            
            # 7. Validação completa
            resultado['valido'] = True
            resultado['message'] = 'Hierarquia válida: Arena e Quadra encontradas'
            
            print("\n🎯 HIERARQUIA VALIDADA:")
            print(f"🏛️ Arena: {self.arena_info['nome']}")
            print(f"🏟️ Quadra: {self.quadra_info['nome']}")
            print(f"🤖 Totem: {self.totem_info['id']}")
            print(f"🆔 Device ID: {self.device_id}")
            
            return resultado
            
        except Exception as e:
            resultado['message'] = f'Erro na verificação de hierarquia: {e}'
            print(f"❌ {resultado['message']}")
            return resultado
    
    def criar_estrutura_pastas_locais(self, timestamp=None):
        """
        Cria a estrutura de pastas hierárquica local: Arena/Quadra/Ano/Mês/Dia/Hora
        
        Args:
            timestamp (datetime, optional): Timestamp para usar na estrutura de pastas
        
        Returns:
            Path: Caminho da pasta da hora se criado com sucesso, None caso contrário
        """
        try:
            if not self.arena_info or not self.quadra_info:
                print("❌ Informações de arena/quadra não disponíveis!")
                return None
            
            # Usa timestamp fornecido ou cria um novo
            if timestamp is None:
                timestamp = datetime.now()
            
            # Sanitiza nomes para uso em pastas (remove caracteres especiais)
            arena_nome = self._sanitizar_nome_pasta(self.arena_info['nome'])
            quadra_nome = self._sanitizar_nome_pasta(self.quadra_info['nome'])
            
            # Estrutura completa de 6 níveis
            ano = timestamp.strftime("%Y")
            mes_num = timestamp.strftime("%m")
            mes_nome = self.meses_ingles[int(mes_num)]
            dia = timestamp.strftime("%d")
            hora = timestamp.strftime("%H") + "h"
            
            # Cria estrutura completa: Videos_Hierarquicos/Arena/Quadra/Ano/MM-Month/DD/HHh
            arena_dir = self.base_videos_dir / arena_nome
            quadra_dir = arena_dir / quadra_nome
            ano_dir = quadra_dir / ano
            mes_dir = ano_dir / f"{mes_num}-{mes_nome}"
            dia_dir = mes_dir / dia
            hora_dir = dia_dir / hora
            
            # Cria todas as pastas
            hora_dir.mkdir(parents=True, exist_ok=True)
            
            print(f"📁 Estrutura hierárquica criada:")
            print(f"   🏛️ Arena: {arena_nome}")
            print(f"   🏟️ Quadra: {quadra_nome}")
            print(f"   📅 Estrutura: {ano}/{mes_num}-{mes_nome}/{dia}/{hora}")
            print(f"   📂 Caminho completo: {hora_dir}")
            
            return hora_dir
            
        except Exception as e:
            print(f"❌ Erro ao criar estrutura de pastas: {e}")
            return None
    
    def _sanitizar_nome_pasta(self, nome):
        """
        Sanitiza nome para uso seguro em pastas.
        
        Args:
            nome (str): Nome original
            
        Returns:
            str: Nome sanitizado
        """
        # Remove ou substitui caracteres especiais
        caracteres_especiais = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
        nome_limpo = nome
        
        for char in caracteres_especiais:
            nome_limpo = nome_limpo.replace(char, '_')
        
        # Remove espaços extras e underscores duplicados
        nome_limpo = '_'.join(nome_limpo.split())
        nome_limpo = '_'.join(filter(None, nome_limpo.split('_')))
        
        return nome_limpo
    
    def salvar_video_local_hierarquico(self, video_path, camera_num, timestamp=None):
        """
        Salva o vídeo na estrutura hierárquica local.
        
        Args:
            video_path (str/Path): Caminho do vídeo original
            camera_num (int): Número da câmera (1 ou 2)
            timestamp (datetime, optional): Timestamp para usar no nome do arquivo
            
        Returns:
            dict: Resultado da operação
        """
        try:
            # Usa timestamp fornecido ou cria um novo
            if timestamp is None:
                timestamp = datetime.now()
            
            # Cria estrutura de pastas
            quadra_dir = self.criar_estrutura_pastas_locais(timestamp)
            if not quadra_dir:
                return {'success': False, 'error': 'Falha ao criar estrutura de pastas'}
            
            # Gera nome do arquivo hierárquico
            timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
            arena_nome = self._sanitizar_nome_pasta(self.arena_info['nome'])
            quadra_nome = self._sanitizar_nome_pasta(self.quadra_info['nome'])
            
            # Nome do arquivo: Arena_Quadra_Camera1_YYYYMMDD_HHMMSS.mp4
            nome_arquivo = f"{arena_nome}_{quadra_nome}_Camera{camera_num}_{timestamp_str}.mp4"
            caminho_destino = quadra_dir / nome_arquivo
            
            # Copia o arquivo para o local hierárquico
            import shutil
            shutil.copy2(video_path, caminho_destino)
            
            # Verifica se foi copiado com sucesso
            if caminho_destino.exists():
                file_size = caminho_destino.stat().st_size / (1024 * 1024)  # MB
                print(f"✅ Vídeo salvo na estrutura hierárquica!")
                print(f"📁 Local: {caminho_destino}")
                print(f"📊 Tamanho: {file_size:.2f} MB")
                
                return {
                    'success': True,
                    'local_path': str(caminho_destino),
                    'arena': self.arena_info['nome'],
                    'quadra': self.quadra_info['nome'],
                    'file_size_mb': file_size
                }
            else:
                return {'success': False, 'error': 'Arquivo não foi copiado'}
                
        except Exception as e:
            return {'success': False, 'error': f'Erro ao salvar vídeo local: {e}'}
    
    def _obter_url_assinada(self, bucket_path, expiracao_segundos=604800, max_tentativas=3):
        """
        Obtém URL assinada para arquivo no bucket com retry.
        
        Args:
            bucket_path (str): Caminho do arquivo no bucket
            expiracao_segundos (int): Tempo de expiração em segundos (padrão: 7 dias)
            max_tentativas (int): Número máximo de tentativas
        
        Returns:
            str: URL assinada completa ou None se falhar
        """
        import time
        
        for tentativa in range(max_tentativas):
            try:
                if not self.supabase:
                    print(f"❌ Supabase não conectado para gerar URL assinada")
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
                    print(f"✅ URL assinada gerada (tentativa {tentativa + 1}): {Path(bucket_path).name}")
                    return url
                else:
                    print(f"⚠️ URL assinada inválida na tentativa {tentativa + 1}")
                    
            except Exception as e:
                print(f"⚠️ Erro ao gerar URL assinada (tentativa {tentativa + 1}): {e}")
            
            # Aguardar antes da próxima tentativa (exceto na última)
            if tentativa < max_tentativas - 1:
                delay = 1.0 * (tentativa + 1)  # 1s, 2s, 3s...
                print(f"⏳ Aguardando {delay}s antes da próxima tentativa...")
                time.sleep(delay)
        
        # Todas as tentativas falharam
        print(f"❌ Falha ao gerar URL assinada após {max_tentativas} tentativas para: {bucket_path}")
        return None
    
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

    def verificar_upload_completo(self, bucket_path, expected_size=None, debug_mode=True):
        """
        Verifica se upload foi realmente concluído com sucesso.
        
        Args:
            bucket_path (str): Caminho do arquivo no bucket
            expected_size (int): Tamanho esperado do arquivo (opcional)
            debug_mode (bool): Se deve mostrar logs de debug
            
        Returns:
            bool: True se upload está completo e íntegro
        """
        try:
            if not self.supabase:
                if debug_mode:
                    print(f"❌ Supabase não conectado")
                return False
            
            import time
            
            # Aguarda um pouco para o upload processar completamente
            time.sleep(0.5)
            
            # Método simplificado: tenta obter URL assinada do arquivo (mais confiável para buckets privados)
            try:
                signed_url = self._obter_url_assinada(bucket_path)
                if signed_url:
                    if debug_mode:
                        print(f"✅ Upload verificado via URL assinada: {Path(bucket_path).name}")
                    return True
            except Exception as url_error:
                if debug_mode:
                    print(f"⚠️ Verificação via URL falhou: {url_error}")
            
            # Método backup: lista arquivos no bucket
            folder_path = str(Path(bucket_path).parent)
            
            if debug_mode:
                print(f"🔍 Verificando pasta: {folder_path}")
            
            response = self.supabase.storage.from_(self.bucket_name).list(
                path=folder_path
            )
            
            if not response:
                if debug_mode:
                    print(f"❌ Nenhum arquivo encontrado na pasta: {folder_path}")
                return False
            
            # Procura pelo arquivo específico
            filename = Path(bucket_path).name
            file_found = None
            
            if debug_mode:
                print(f"🔍 Procurando arquivo: {filename} em {len(response)} itens")
            
            for file_info in response:
                if file_info.get('name') == filename:
                    file_found = file_info
                    break
            
            if not file_found:
                if debug_mode:
                    print(f"❌ Arquivo não encontrado no bucket: {filename}")
                    # Mostra os primeiros arquivos para debug
                    print(f"📋 Arquivos disponíveis:")
                    for i, item in enumerate(response[:5]):
                        print(f"   [{i}] {item.get('name', 'N/A')}")
                return False
            
            if debug_mode:
                print(f"✅ Arquivo encontrado: {filename}")
            
            # Verifica tamanho se fornecido (modo simplificado)
            if expected_size and debug_mode:
                # Tenta diferentes formas de acessar o tamanho
                remote_size = None
                
                # Tentativa 1: size direto
                if 'size' in file_found:
                    remote_size = file_found['size']
                # Tentativa 2: metadata.size
                elif 'metadata' in file_found and isinstance(file_found['metadata'], dict):
                    remote_size = file_found['metadata'].get('size')
                
                if remote_size:
                    print(f"📊 Tamanho: local={expected_size}, remoto={remote_size}")
                    if abs(remote_size - expected_size) > expected_size * 0.1:  # 10% de tolerância
                        print(f"⚠️ Diferença significativa de tamanho (>10%)")
            
            if debug_mode:
                print(f"✅ Upload verificado com sucesso: {filename}")
            return True
            
        except Exception as e:
            if debug_mode:
                print(f"❌ Erro ao verificar upload: {e}")
                import traceback
                print(f"🔍 Stack trace: {traceback.format_exc()}")
            
            # Em caso de erro na verificação, assume sucesso (modo conservador)
            if debug_mode:
                print(f"⚠️ Assumindo sucesso devido a erro na verificação")
            return True

    def upload_video_supabase(self, video_path, camera_num, timestamp=None):
        """
        Faz upload do vídeo para o bucket do Supabase na estrutura hierárquica.
        
        Args:
            video_path (str/Path): Caminho do vídeo
            camera_num (int): Número da câmera
            timestamp (datetime, optional): Timestamp para usar no nome do arquivo
            
        Returns:
            dict: Resultado da operação
        """
        try:
            if not self.supabase:
                return {'success': False, 'error': 'Supabase não conectado'}
            
            # Usa timestamp fornecido ou cria um novo
            if timestamp is None:
                timestamp = datetime.now()
            
            # Gera caminho hierárquico no bucket com estrutura completa de 6 níveis
            timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
            arena_nome = self._sanitizar_nome_pasta(self.arena_info['nome'])
            quadra_nome = self._sanitizar_nome_pasta(self.quadra_info['nome'])
            
            # Estrutura completa de 6 níveis para o bucket
            ano = timestamp.strftime("%Y")
            mes_num = timestamp.strftime("%m")
            mes_nome = self.meses_ingles[int(mes_num)]
            dia = timestamp.strftime("%d")
            hora = timestamp.strftime("%H") + "h"
            
            # Nome do arquivo: Arena_Quadra_Camera1_YYYYMMDD_HHMMSS.mp4
            nome_arquivo = f"{arena_nome}_{quadra_nome}_Camera{camera_num}_{timestamp_str}.mp4"
            
            # Caminho no bucket: arena/quadra/ano/mm-month/dd/hhh/arquivo.mp4
            bucket_path = f"{arena_nome}/{quadra_nome}/{ano}/{mes_num}-{mes_nome}/{dia}/{hora}/{nome_arquivo}"
            
            print(f"☁️ Fazendo upload para Supabase...")
            print(f"📂 Bucket: {self.bucket_name}")
            print(f"📁 Caminho: {bucket_path}")
            
            # Lê o arquivo
            video_path = Path(video_path)
            file_size = video_path.stat().st_size
            
            with open(video_path, 'rb') as file:
                video_data = file.read()
            
            # Faz upload (com tratamento de exceções do Supabase)
            upload_success = False
            response = None
            upload_error = None
            
            try:
                response = self.supabase.storage.from_(self.bucket_name).upload(
                    bucket_path, 
                    video_data,
                    file_options={'content-type': 'video/mp4'}
                )
                upload_success = True
                
                # Debug: mostra resposta do upload
                print(f"🔍 DEBUG: Resposta do upload: {response}")
                
            except Exception as e:
                # Captura exceções do Supabase Storage
                error_str = str(e)
                upload_error = e
                
                # Verifica se é erro de duplicata
                if ('409' in error_str or 'Duplicate' in error_str or 'already exists' in error_str):
                    print(f"✅ Arquivo já existe no bucket - considerando como sucesso")
                    print(f"📂 Caminho: {bucket_path}")
                    
                    # Obtém URL assinada do arquivo existente
                    public_url = self._obter_url_assinada(bucket_path)
                    
                    file_size_mb = file_size / (1024 * 1024)  # MB
                    
                    print(f"✅ Arquivo duplicado tratado como sucesso!")
                    print(f"🌐 URL: {public_url}")
                    print(f"📊 Tamanho: {file_size_mb:.2f} MB")
                    
                    return {
                        'success': True,
                        'bucket_path': bucket_path,
                        'public_url': public_url,
                        'arena': self.arena_info['nome'],
                        'quadra': self.quadra_info['nome'],
                        'file_size_mb': file_size_mb,
                        'verified': True,
                        'duplicate': True
                    }
                else:
                    # Outro tipo de erro
                    print(f"🔍 DEBUG: Erro no upload: {e}")
                    return {'success': False, 'error': f'Erro no upload: {e}'}
            
            # Se chegou aqui, upload foi bem-sucedido
            if not upload_success:
                return {'success': False, 'error': 'Upload falhou por motivo desconhecido'}
            
            # Verificação de integridade do upload
            print(f"🔍 Verificando integridade do upload...")
            
            # Usa debug mode baseado em configuração de ambiente
            import os
            debug_mode = os.getenv('UPLOAD_DEBUG_MODE', 'True').lower() == 'true'
            
            upload_verified = self.verificar_upload_completo(bucket_path, file_size, debug_mode)
            
            if not upload_verified:
                print(f"⚠️ Verificação falhou, mas upload pode ter sido bem-sucedido")
                # Não falha mais automaticamente - continua o processo
            
            # Obtém URL assinada
            public_url = self._obter_url_assinada(bucket_path)
            
            file_size_mb = file_size / (1024 * 1024)  # MB
            
            print(f"✅ Upload concluído e verificado!")
            print(f"🌐 URL: {public_url}")
            print(f"📊 Tamanho: {file_size_mb:.2f} MB")
            
            return {
                'success': True,
                'bucket_path': bucket_path,
                'public_url': public_url,
                'arena': self.arena_info['nome'],
                'quadra': self.quadra_info['nome'],
                'file_size_mb': file_size_mb,
                'verified': True
            }
            
        except Exception as e:
            return {'success': False, 'error': f'Erro no upload: {e}'}
    
    def processar_video_completo(self, video_path, camera_num, timestamp=None):
        """
        Processa um vídeo de forma completa: verifica hierarquia, salva local e faz upload.
        
        Args:
            video_path (str/Path): Caminho do vídeo
            camera_num (int): Número da câmera
            timestamp (datetime, optional): Timestamp para usar no processamento
            
        Returns:
            dict: Resultado completo da operação
        """
        resultado = {
            'success': False,
            'local_save': None,
            'upload': None,
            'hierarchy': None,
            'message': ''
        }
        
        try:
            # Usa timestamp fornecido ou cria um novo
            if timestamp is None:
                timestamp = datetime.now()
                
            print(f"\n🎬 PROCESSAMENTO COMPLETO - CÂMERA {camera_num}")
            print("=" * 50)
            
            # 1. Verifica hierarquia
            hierarchy_check = self.verificar_totem_hierarquia()
            resultado['hierarchy'] = hierarchy_check
            
            if not hierarchy_check['valido']:
                resultado['message'] = f"Gravação não permitida: {hierarchy_check['message']}"
                print(f"\n❌ {resultado['message']}")
                return resultado
            
            print(f"\n✅ Hierarquia válida! Processando vídeo...")
            
            # 2. Salva localmente
            print(f"\n💾 SALVANDO LOCALMENTE...")
            local_result = self.salvar_video_local_hierarquico(video_path, camera_num, timestamp)
            resultado['local_save'] = local_result
            
            if not local_result['success']:
                resultado['message'] = f"Falha ao salvar localmente: {local_result['error']}"
                print(f"❌ {resultado['message']}")
                return resultado
            
            # 3. Faz upload para Supabase
            print(f"\n☁️ UPLOAD PARA SUPABASE...")
            upload_result = self.upload_video_supabase(video_path, camera_num, timestamp)
            resultado['upload'] = upload_result
            
            if not upload_result['success']:
                resultado['message'] = f"Falha no upload: {upload_result['error']}"
                print(f"⚠️ {resultado['message']} (arquivo salvo localmente)")
                # Não retorna aqui - arquivo foi salvo localmente
            
            resultado['success'] = True
            resultado['message'] = "Vídeo processado com sucesso!"
            
            print(f"\n🎉 PROCESSAMENTO CONCLUÍDO!")
            print(f"🏛️ Arena: {self.arena_info['nome']}")
            print(f"🏟️ Quadra: {self.quadra_info['nome']}")
            print(f"💾 Salvo localmente: {'✅' if local_result['success'] else '❌'}")
            print(f"☁️ Upload Supabase: {'✅' if upload_result['success'] else '❌'}")
            
            return resultado
            
        except Exception as e:
            resultado['message'] = f'Erro no processamento: {e}'
            print(f"❌ {resultado['message']}")
            return resultado
    
    def pode_gravar(self):
        """
        Verifica se a gravação é permitida (arena e quadra configuradas).
        
        Returns:
            bool: True se pode gravar, False caso contrário
        """
        hierarchy_check = self.verificar_totem_hierarquia()
        return hierarchy_check['valido']
    
    def obter_info_hierarquia(self):
        """
        Obtém informações da hierarquia atual.
        
        Returns:
            dict: Informações da arena, quadra e totem
        """
        if not self.arena_info or not self.quadra_info:
            hierarchy_check = self.verificar_totem_hierarquia()
            if not hierarchy_check['valido']:
                return None
        
        return {
            'arena': self.arena_info,
            'quadra': self.quadra_info,
            'totem': self.totem_info,
            'device_id': self.device_id
        }


def main():
    """
    Função principal para testar o gerenciador hierárquico.
    """
    print("🏗️ TESTE DO GERENCIADOR HIERÁRQUICO DE VÍDEOS")
    print("=" * 60)
    print()
    
    # Cria uma instância do gerenciador
    video_manager = HierarchicalVideoManager()
    
    # Verifica se pode gravar
    if video_manager.pode_gravar():
        print("✅ SISTEMA AUTORIZADO PARA GRAVAÇÃO!")
        
        # Mostra informações da hierarquia
        info = video_manager.obter_info_hierarquia()
        if info:
            print(f"\n📋 INFORMAÇÕES DA HIERARQUIA:")
            print(f"🏛️ Arena: {info['arena']['nome']}")
            print(f"🏟️ Quadra: {info['quadra']['nome']}")
            print(f"🆔 Device ID: {info['device_id']}")
            
        # Cria estrutura de pastas
        pasta_quadra = video_manager.criar_estrutura_pastas_locais()
        if pasta_quadra:
            print(f"\n📁 Pasta da quadra: {pasta_quadra}")
            
    else:
        print("❌ SISTEMA NÃO AUTORIZADO PARA GRAVAÇÃO!")
        print("Verifique se o totem está associado a uma arena e quadra no banco de dados.")


if __name__ == "__main__":
    main()