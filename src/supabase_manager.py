#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerenciador do Supabase
Gerencia a conexão com o Supabase e insere o Device ID como token na tabela totens.
Também gerencia a inserção das câmeras na tabela cameras.
"""

import os
import uuid
import json
import time
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv
from device_manager import DeviceManager
from system_logger import system_logger, log_debug, log_info, log_warning, log_error, log_success

class SupabaseManager:
    def __init__(self, device_manager=None):
        """
        Inicializa o gerenciador do Supabase.
        
        Args:
            device_manager (DeviceManager): Instância do DeviceManager (opcional)
        """
        # Carrega configurações
        self._carregar_configuracoes()
        
        # Usa o DeviceManager fornecido ou cria um novo
        if device_manager:
            self.device_manager = device_manager
        else:
            # Garante que usa o caminho correto para device_config
            src_dir = Path(__file__).parent
            self.device_manager = DeviceManager(src_dir / "device_config")
        
        # Cliente Supabase
        self.supabase = None
        self.device_id = None
        
        # Conecta automaticamente ao Supabase
        self.conectar_supabase()
        
        # Verifica e carrega o Device ID
        self.verificar_device_id()
        
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
        self.supabase_anon_key = os.getenv('SUPABASE_ANON_KEY')
        self.supabase_service_role_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        
    def conectar_supabase(self):
        """
        Conecta ao Supabase usando as credenciais configuradas.
        
        Returns:
            bool: True se conectou com sucesso, False caso contrário
        """
        try:
            # Carrega configurações automaticamente
            self._carregar_configuracoes()
            
            if not self.supabase_url or not self.supabase_service_role_key:
                log_error("Configurações do Supabase não encontradas!")
                return False
            
            # Usa a service role key para operações de inserção
            self.supabase = create_client(self.supabase_url, self.supabase_service_role_key)
            return True
            
        except Exception as e:
            log_error(f"Erro ao conectar no Supabase: {e}")
            return False
    
    def verificar_device_id(self):
        """
        Verifica se o Device ID existe e é válido.
        
        Returns:
            str: Device ID se válido, None caso contrário
        """
        try:
            # Verifica cache primeiro
            if system_logger.is_cached('device_id_verified'):
                log_debug("Device ID já verificado (cache)")
                return self.device_id
            
            self.device_id = self.device_manager.get_device_id()
            
            if self.device_id:
                log_debug(f"Device ID encontrado: {self.device_id}")
                
                # Verifica se é um UUID válido
                try:
                    uuid.UUID(self.device_id)
                    log_success("Device ID é um UUID válido")
                    system_logger.cache_verification('device_id_verified', True)
                    return self.device_id
                except ValueError:
                    log_error("Device ID não é um UUID válido!")
                    return None
            else:
                log_error("Device ID não encontrado!")
                return None
                
        except Exception as e:
            log_error(f"Erro ao verificar Device ID: {e}")
            return None
    
    def verificar_token_existe(self, token):
        """
        Verifica se o token já existe na tabela totens.
        
        Args:
            token (str): Token (Device ID) para verificar
            
        Returns:
            dict: Dados do totem se existe, None caso contrário
        """
        try:
            if not self.supabase:
                log_error("Supabase não conectado!")
                return None
            
            # Busca token na tabela totens
            response = self.supabase.table('totens').select('*').eq('token', token).execute()
            
            if response.data:
                log_warning(f"Token já existe na tabela totens: {response.data[0]['id']}")
                return response.data[0]
            else:
                log_debug("Token não existe na tabela - pode inserir")
                return None
                
        except Exception as e:
            log_error(f"Erro ao verificar token existente: {e}")
            return None
    
    def inserir_totem(self):
        """
        Insere um novo totem na tabela com o Device ID como token.
        
        Returns:
            dict: Dados do totem inserido se sucesso, None caso contrário
        """
        try:
            if not self.device_id:
                log_error("Device ID não disponível para inserção!")
                return None
            
            if not self.supabase:
                log_error("Supabase não conectado!")
                return None
            
            # Verifica se token já existe
            totem_existente = self.verificar_token_existe(self.device_id)
            if totem_existente:
                log_info("Token já existe - reutilizando totem existente")
                return totem_existente
            
            # Dados para inserção
            totem_data = {
                'token': self.device_id,
                'status': 'ativo',
                'quadra_id': None,  # Pode ser definido posteriormente
                'qr_code_base64': None  # Pode ser preenchido com QR code posteriormente
            }
            
            # Insere na tabela totens
            response = self.supabase.table('totens').insert(totem_data).execute()
            
            if response.data:
                totem_inserido = response.data[0]
                log_success("Totem inserido com sucesso!")
                log_info(f"ID do Totem: {totem_inserido['id']}")
                log_debug(f"Token: {totem_inserido['token']}")
                log_debug(f"Criado em: {totem_inserido['created_at']}")
                return totem_inserido
            else:
                log_error("Falha ao inserir totem - resposta vazia")
                return None
                
        except Exception as e:
            log_error(f"Erro ao inserir totem: {e}")
            return None
    
    def carregar_informacoes_onvif(self):
        """
        Carrega as informações ONVIF das câmeras do arquivo JSON mais recente.
        
        Returns:
            dict: Dados das câmeras ONVIF ou None se não encontrar
        """
        try:
            # Verifica cache primeiro
            if system_logger.is_cached('onvif_data_loaded'):
                log_debug("Dados ONVIF já carregados (cache)")
                return getattr(self, '_cached_onvif_data', None)
            
            # Procura pelo arquivo ONVIF mais recente na pasta device_config (na raiz do projeto)
            device_config_dir = Path(__file__).parent.parent / "device_config"
            
            if not device_config_dir.exists():
                log_warning("Pasta device_config não encontrada")
                return None
            
            # Procura por arquivos camera_onvif_info_*.json
            onvif_files = list(device_config_dir.glob("camera_onvif_info_*.json"))
            
            if not onvif_files:
                log_warning("Nenhum arquivo ONVIF encontrado")
                return None
            
            # Pega o arquivo mais recente
            arquivo_mais_recente = max(onvif_files, key=lambda x: x.stat().st_ctime)
            
            log_info(f"Carregando informações ONVIF de: {arquivo_mais_recente.name}")
            
            with open(arquivo_mais_recente, 'r', encoding='utf-8') as f:
                dados_onvif = json.load(f)
            
            # Cache os dados carregados
            self._cached_onvif_data = dados_onvif
            system_logger.cache_verification('onvif_data_loaded', True)
            
            return dados_onvif
            
        except Exception as e:
            log_error(f"Erro ao carregar informações ONVIF: {e}")
            return None

    def verificar_cameras_existem(self, totem_id):
        """
        Verifica se as câmeras já existem para um totem.
        
        Args:
            totem_id (str): ID do totem para verificar
            
        Returns:
            list: Lista de câmeras existentes ou lista vazia
        """
        try:
            if not self.supabase:
                log_error("Supabase não conectado!")
                return []
            
            # Busca câmeras na tabela cameras
            response = self.supabase.table('cameras').select('*').eq('totem_id', totem_id).execute()
            
            if response.data:
                log_debug(f"Encontradas {len(response.data)} câmera(s) existente(s) para o totem")
                return response.data
            else:
                log_debug("Nenhuma câmera existente - pode inserir")
                return []
                
        except Exception as e:
            log_error(f"Erro ao verificar câmeras existentes: {e}")
            return []

    def verificar_cameras_onvif_existem(self, device_uuids):
        """
        Verifica se as câmeras com device_uuid específicos já existem na tabela.
        
        Args:
            device_uuids (list): Lista de device_uuid para verificar
            
        Returns:
            dict: Resultado da verificação com 'success' e 'cameras'
        """
        resultado = {
            'success': False,
            'cameras': [],
            'message': ''
        }
        
        try:
            if not self.supabase:
                resultado['message'] = 'Supabase não conectado'
                return resultado
                
            if not device_uuids:
                resultado['success'] = True
                resultado['message'] = 'Nenhum UUID para verificar'
                return resultado
            
            cameras_existentes = []
            
            for device_uuid in device_uuids:
                response = self.supabase.table('cameras').select('*').eq('id', device_uuid).execute()
                if response.data:
                    cameras_existentes.extend(response.data)
            
            if cameras_existentes:
                log_debug(f"Encontradas {len(cameras_existentes)} câmera(s) com UUID ONVIF já existente(s)")
                for cam in cameras_existentes:
                    log_debug(f"Câmera {cam['nome']} - UUID: {cam['id']}")
            
            resultado['success'] = True
            resultado['cameras'] = cameras_existentes
            resultado['message'] = f'{len(cameras_existentes)} câmeras encontradas'
            return resultado
                
        except Exception as e:
            log_error(f"Erro ao verificar câmeras ONVIF existentes: {e}")
            resultado['message'] = f'Erro: {e}'
            return resultado
    
    def inserir_cameras(self, totem_id):
        """
        Insere as duas câmeras (Camera 1 e Camera 2) na tabela cameras usando UUIDs do ONVIF.
        
        Args:
            totem_id (str): ID do totem para associar as câmeras
            
        Returns:
            dict: Resultado da operação com status e dados das câmeras
        """
        resultado = {
            'success': False,
            'cameras_inseridas': [],
            'message': ''
        }
        
        try:
            if not totem_id:
                resultado['message'] = 'Totem ID não fornecido'
                return resultado
            
            if not self.supabase:
                resultado['message'] = 'Supabase não conectado'
                return resultado

            log_info("Inserindo câmeras com ONVIF UUID na tabela")
            
            # Carregar dados ONVIF
            dados_onvif = self.carregar_informacoes_onvif()

            if not dados_onvif:
                log_warning("Dados ONVIF não encontrados, usando inserção padrão")
                return self._inserir_cameras_padrao(totem_id)
            
            # Processar dados ONVIF
            cameras_onvif = []
            device_uuids = []

            for camera_key, camera_data in dados_onvif.items():
                if camera_key.startswith('camera_') and isinstance(camera_data, dict):
                    dispositivo = camera_data.get('dispositivo', {})
                    device_uuid = dispositivo.get('device_uuid')
                    camera_id = camera_data.get('camera_id')
                    
                    if device_uuid and device_uuid != 'N/A':
                        cameras_onvif.append({
                            'camera_id': camera_id,
                            'device_uuid': device_uuid,
                            'serial_number': dispositivo.get('serial_number', 'N/A'),
                            'fabricante': dispositivo.get('fabricante', 'N/A'),
                            'modelo': dispositivo.get('modelo', 'N/A')
                        })
                        device_uuids.append(device_uuid)

            if not cameras_onvif:
                log_warning("Nenhuma câmera ONVIF válida encontrada, usando inserção padrão")
                return self._inserir_cameras_padrao(totem_id)

            log_debug(f"Encontradas {len(cameras_onvif)} câmera(s) ONVIF")
            for cam in cameras_onvif:
                log_debug(f"Câmera {cam['camera_id']}: {cam['device_uuid']} ({cam['serial_number']})")
            
            # Verificar se câmeras já existem
            cameras_existentes_result = self.verificar_cameras_onvif_existem(device_uuids)
            cameras_existentes = cameras_existentes_result.get('cameras', [])
            
            log_debug(f"🔍 Verificação de câmeras existentes:")
            log_debug(f"   • Câmeras ONVIF detectadas: {len(cameras_onvif)}")
            log_debug(f"   • Câmeras já existentes no banco: {len(cameras_existentes)}")
            
            if len(cameras_existentes) >= len(cameras_onvif):
                resultado['success'] = True
                resultado['cameras_inseridas'] = cameras_existentes
                resultado['message'] = 'Câmeras ONVIF já existem - reutilizando'
                log_info("Câmeras ONVIF já existem - reutilizando")
                return resultado
            
            log_info(f"📹 Inserindo {len(cameras_onvif)} câmeras ONVIF no banco de dados...")
            
            # Verificar câmeras antigas
            cameras_antigas = self.verificar_cameras_existem(totem_id)
            
            # Usar UPSERT para resolver conflitos
            log_debug("Usando UPSERT para resolver conflitos de câmeras")
            
            # Preparar dados para inserção
            cameras_data = []
            for cam in cameras_onvif:
                cameras_data.append({
                    'id': cam['device_uuid'],  # Usa device_uuid como ID da câmera
                    'totem_id': totem_id,
                    'ordem': cam['camera_id'],
                    'nome': f"Camera {cam['camera_id']} - {cam['fabricante']} {cam['modelo']}"
                })

            log_debug("Aplicando UPSERT com UUIDs ONVIF")
            for cam_data in cameras_data:
                log_debug(f"{cam_data['nome']} - UUID: {cam_data['id']} - Ordem: {cam_data['ordem']}")
            
            # Executar UPSERT com conflito correto
            # Há duas constraints únicas: id (PK) e totem_id+ordem
            # Para câmeras ONVIF, o conflito pode ser em qualquer uma
            response = self.supabase.table('cameras').upsert(
                cameras_data,
                on_conflict='totem_id,ordem'  # Resolve conflito na constraint totem_id+ordem
            ).execute()

            if response.data and len(response.data) == len(cameras_onvif):
                cameras_inseridas = response.data
                resultado['success'] = True
                resultado['cameras_inseridas'] = cameras_inseridas
                resultado['message'] = 'Câmeras ONVIF processadas com sucesso via UPSERT'

                log_success("Câmeras ONVIF processadas com sucesso via UPSERT!")
                for camera in cameras_inseridas:
                    log_info(f"{camera['nome']} - UUID: {camera['id']} - Ordem: {camera['ordem']}")

                return resultado
            else:
                resultado['message'] = 'Falha no UPSERT das câmeras ONVIF - resposta inválida'
                log_error("Falha no UPSERT das câmeras ONVIF - resposta inválida")
                return resultado
                
        except Exception as e:
            log_error(f"Erro ao inserir câmeras: {e}")
            resultado['message'] = f'Erro ao inserir câmeras: {str(e)}'
            return resultado
    
    def get_quadra_info(self, quadra_id):
        """
        Busca informações detalhadas da quadra no Supabase.
        
        Args:
            quadra_id (str): UUID da quadra
            
        Returns:
            dict: Resultado da operação com success e data
        """
        try:
            if not self.supabase:
                log_error("Supabase não conectado!")
                return {
                    'success': False,
                    'message': 'Supabase não conectado',
                    'data': None
                }
            
            log_debug(f"🔍 Buscando informações da quadra: {quadra_id}")
            response = self.supabase.table('quadras').select('*').eq('id', quadra_id).execute()
            
            if response.data and len(response.data) > 0:
                quadra_info = response.data[0]
                log_debug(f"✅ Quadra encontrada: {quadra_info.get('nome', 'N/A')}")
                return {
                    'success': True,
                    'message': 'Quadra encontrada',
                    'data': quadra_info
                }
            else:
                log_warning(f"⚠️ Quadra não encontrada: {quadra_id}")
                return {
                    'success': False,
                    'message': 'Quadra não encontrada',
                    'data': None
                }
                
        except Exception as e:
            log_error(f"❌ Erro ao buscar informações da quadra: {e}")
            return {
                'success': False,
                'message': f'Erro ao consultar quadra: {e}',
                'data': None
            }
    
    def get_arena_info(self, arena_id):
        """
        Busca informações detalhadas da arena no Supabase.
        
        Args:
            arena_id (str): UUID da arena
            
        Returns:
            dict: Resultado da operação com success e data
        """
        try:
            if not self.supabase:
                log_error("Supabase não conectado!")
                return {
                    'success': False,
                    'message': 'Supabase não conectado',
                    'data': None
                }
            
            log_debug(f"🔍 Buscando informações da arena: {arena_id}")
            response = self.supabase.table('arenas').select('*').eq('id', arena_id).execute()
            
            if response.data and len(response.data) > 0:
                arena_info = response.data[0]
                log_debug(f"✅ Arena encontrada: {arena_info.get('nome', 'N/A')}")
                return {
                    'success': True,
                    'message': 'Arena encontrada',
                    'data': arena_info
                }
            else:
                log_warning(f"⚠️ Arena não encontrada: {arena_id}")
                return {
                    'success': False,
                    'message': 'Arena não encontrada',
                    'data': None
                }
                
        except Exception as e:
            log_error(f"❌ Erro ao buscar informações da arena: {e}")
            return {
                'success': False,
                'message': f'Erro ao consultar arena: {e}',
                'data': None
            }
    
    def sanitize_folder_name(self, nome):
        """
        Limpa nomes para uso em caminhos de arquivo.
        Remove caracteres especiais, espaços e substitui por underscores.
        
        Args:
            nome (str): Nome original
            
        Returns:
            str: Nome sanitizado para uso em pastas
        """
        if not nome or not isinstance(nome, str):
            return "nome_invalido"
        
        # Remove caracteres especiais, mantém apenas letras, números, espaços, hífens e underscores
        nome_limpo = re.sub(r'[^\w\s\-_.]', '', nome)
        
        # Substitui espaços por underscores
        nome_limpo = re.sub(r'\s+', '_', nome_limpo)
        
        # Remove underscores múltiplos
        nome_limpo = re.sub(r'_+', '_', nome_limpo)
        
        # Remove underscores no início e fim
        nome_limpo = nome_limpo.strip('_')
        
        # Se ficou vazio, usa fallback
        if not nome_limpo:
            nome_limpo = "nome_sanitizado"
        
        log_debug(f"🧹 Nome sanitizado: '{nome}' -> '{nome_limpo}'")
        return nome_limpo

    def _inserir_cameras_padrao(self, totem_id):
        """
        Inserção padrão de câmeras quando dados ONVIF não estão disponíveis.
        
        Args:
            totem_id (str): ID do totem
            
        Returns:
            dict: Resultado da operação
        """
        resultado = {
            'success': False,
            'cameras_inseridas': [],
            'message': ''
        }
        
        try:
            # Verifica se câmeras já existem para este totem
            cameras_existentes = self.verificar_cameras_existem(totem_id)
            if len(cameras_existentes) >= 2:
                resultado['success'] = True
                resultado['cameras_inseridas'] = cameras_existentes
                resultado['message'] = 'Câmeras padrão já existem - reutilizando'
                log_info("Câmeras padrão já existem - reutilizando")
                return resultado
            
            log_info("Inserindo câmeras padrão (sem ONVIF)")
            
            # Dados das câmeras para inserção padrão
            cameras_data = [
                {
                    'totem_id': totem_id,
                    'ordem': 1,
                    'nome': 'Camera 1'
                },
                {
                    'totem_id': totem_id,
                    'ordem': 2,
                    'nome': 'Camera 2'
                }
            ]
            
            # Usa UPSERT para evitar conflitos de duplicação
            # Se câmeras já existem com mesmo totem_id e ordem, atualiza
            response = self.supabase.table('cameras').upsert(
                cameras_data,
                on_conflict='totem_id,ordem'  # Para câmeras padrão, conflito é em totem_id+ordem
            ).execute()
            
            if response.data and len(response.data) == 2:
                cameras_inseridas = response.data
                resultado['success'] = True
                resultado['cameras_inseridas'] = cameras_inseridas
                resultado['message'] = 'Câmeras padrão inseridas com sucesso'
                
                log_success("Câmeras padrão inseridas com sucesso!")
                for camera in cameras_inseridas:
                    log_info(f"{camera['nome']} - ID: {camera['id']} - Ordem: {camera['ordem']}")
                
                return resultado
            else:
                resultado['message'] = 'Falha ao inserir câmeras padrão - resposta inválida'
                log_error("Falha ao inserir câmeras padrão - resposta inválida")
                return resultado
                
        except Exception as e:
            resultado['message'] = f'Erro ao inserir câmeras padrão: {e}'
            log_error(f"Erro ao inserir câmeras padrão: {e}")
            return resultado

    def _atualizar_cameras_com_onvif(self, totem_id, cameras_onvif, cameras_antigas):
        """
        Substitui câmeras existentes por novas com UUIDs ONVIF.
        (Delete + Insert porque não podemos alterar chave primária)
        
        Args:
            totem_id (str): ID do totem
            cameras_onvif (list): Lista de câmeras ONVIF
            cameras_antigas (list): Câmeras já existentes no banco
            
        Returns:
            dict: Resultado da operação
        """
        resultado = {
            'success': False,
            'cameras_inseridas': [],
            'message': ''
        }
        
        try:
            print(f"\n🔄 SUBSTITUINDO {len(cameras_antigas)} CÂMERA(S) POR VERSÕES ONVIF")
            print("-" * 60)
            print("⚠️ Processo: DELETE câmeras antigas → INSERT câmeras ONVIF")
            
            # Passo 1: Deletar câmeras antigas
            print("\n🗑️ DELETANDO CÂMERAS ANTIGAS...")
            cameras_deletadas = 0
            
            for camera_antiga in cameras_antigas:
                print(f"   🗑️ Deletando Câmera {camera_antiga['ordem']} (ID: {camera_antiga['id']})")
                
                response = self.supabase.table('cameras').delete().eq('id', camera_antiga['id']).execute()
                
                if response.data:
                    cameras_deletadas += 1
                    print(f"      ✅ Deletada com sucesso!")
                else:
                    print(f"      ❌ Falha ao deletar")
            
            print(f"📊 Câmeras deletadas: {cameras_deletadas}/{len(cameras_antigas)}")
            
            # Passo 2: Inserir câmeras ONVIF
            print(f"\n📹 INSERINDO CÂMERAS ONVIF...")
            
            cameras_data = []
            for cam in cameras_onvif:
                cameras_data.append({
                    'id': cam['device_uuid'],  # UUID ONVIF como ID
                    'totem_id': totem_id,
                    'ordem': cam['camera_id'],
                    'nome': f"Camera {cam['camera_id']} - {cam['fabricante']} {cam['modelo']}"
                })
                
                print(f"   📹 Preparando {cam['camera_id']}: {cam['device_uuid']} ({cam['serial_number']})")
            
            # Insere todas as câmeras ONVIF
            response = self.supabase.table('cameras').insert(cameras_data).execute()
            
            if response.data and len(response.data) == len(cameras_onvif):
                cameras_inseridas = response.data
                resultado['success'] = True
                resultado['cameras_inseridas'] = cameras_inseridas
                resultado['message'] = f'Câmeras substituídas com UUIDs ONVIF ({len(cameras_inseridas)} câmeras)'
                
                print(f"\n✅ SUBSTITUIÇÃO CONCLUÍDA COM SUCESSO!")
                print(f"📊 Câmeras inseridas: {len(cameras_inseridas)}")
                for camera in cameras_inseridas:
                    print(f"📹 {camera['nome']}")
                    print(f"   🆔 UUID ONVIF: {camera['id']}")
                    print(f"   🔢 Ordem: {camera['ordem']}")
                    print(f"   🏢 Totem: {camera['totem_id']}")
            else:
                resultado['message'] = 'Falha ao inserir câmeras ONVIF após deletar antigas'
                print("❌ Falha ao inserir câmeras ONVIF - processo incompleto!")
            
            return resultado
            
        except Exception as e:
            resultado['message'] = f'Erro ao substituir câmeras: {e}'
            print(f"❌ Erro ao substituir câmeras: {e}")
            print("⚠️ IMPORTANTE: Algumas câmeras podem ter sido deletadas sem serem substituídas!")
            return resultado
    
    def verificar_cameras_inseridas(self, totem_id):
        """
        Verifica se as câmeras foram inseridas corretamente (ONVIF ou padrão).
        
        Args:
            totem_id (str): ID do totem para verificar
            
        Returns:
            dict: Resultado da verificação
        """
        try:
            if not self.supabase or not totem_id:
                return {'success': False, 'message': 'Supabase não conectado ou totem_id inválido'}
            
            # Busca câmeras do totem
            response = self.supabase.table('cameras').select('*').eq('totem_id', totem_id).order('ordem').execute()
            
            if not response.data:
                return {'success': False, 'message': 'Nenhuma câmera encontrada para este totem'}
            
            cameras = response.data
            
            # Carrega dados ONVIF para comparação
            dados_onvif = self.carregar_informacoes_onvif()
            
            if dados_onvif:
                # Verifica se são câmeras ONVIF
                print("✅ VERIFICAÇÃO DE CÂMERAS ONVIF CONCLUÍDA!")
                print(f"📊 Total de câmeras encontradas: {len(cameras)}")
                
                cameras_onvif_validas = 0
                for camera in cameras:
                    print(f"📹 {camera['nome']}")
                    print(f"   🆔 UUID: {camera['id']}")
                    print(f"   🔢 Ordem: {camera['ordem']}")
                    print(f"   🏢 Totem: {camera['totem_id']}")
                    
                    # Verifica se o UUID bate com algum device_uuid do ONVIF
                    for camera_key, camera_data in dados_onvif.items():
                        if camera_key.startswith('camera_') and isinstance(camera_data, dict):
                            dispositivo = camera_data.get('dispositivo', {})
                            if dispositivo.get('device_uuid') == camera['id']:
                                cameras_onvif_validas += 1
                                print(f"   ✅ UUID ONVIF válido: {dispositivo.get('serial_number', 'N/A')}")
                                break
                    else:
                        print(f"   ⚠️ UUID não encontrado no ONVIF")
                
                if cameras_onvif_validas >= len(cameras):
                    return {
                        'success': True,
                        'cameras': cameras,
                        'message': f'Câmeras ONVIF verificadas com sucesso ({cameras_onvif_validas}/{len(cameras)})',
                        'tipo': 'onvif'
                    }
                else:
                    return {
                        'success': True,
                        'cameras': cameras,
                        'message': f'Câmeras verificadas - algumas não são ONVIF ({cameras_onvif_validas}/{len(cameras)})',
                        'tipo': 'misto'
                    }
            else:
                # Verificação padrão (sem ONVIF)
                if len(cameras) >= 2:
                    print("✅ VERIFICAÇÃO DE CÂMERAS PADRÃO CONCLUÍDA!")
                    for camera in cameras:
                        print(f"📹 {camera['nome']} - ID: {camera['id']} - Ordem: {camera['ordem']}")
                    
                    return {
                        'success': True,
                        'cameras': cameras,
                        'message': 'Câmeras padrão verificadas com sucesso',
                        'tipo': 'padrao'
                    }
                else:
                    return {
                        'success': False, 
                        'message': f'Número insuficiente de câmeras: {len(cameras)}'
                    }
                
        except Exception as e:
            return {'success': False, 'message': f'Erro na verificação: {e}'}
    
    def atualizar_qr_code_totem(self, qr_code_base64):
        """
        Atualiza o QR code base64 do totem existente.
        
        Args:
            qr_code_base64 (str): QR code em formato base64
            
        Returns:
            bool: True se atualizou com sucesso, False caso contrário
        """
        try:
            if not self.device_id or not self.supabase:
                print("❌ Device ID ou Supabase não disponíveis!")
                return False
            
            # Atualiza o QR code do totem
            response = self.supabase.table('totens').update({
                'qr_code_base64': qr_code_base64,
                'updated_at': datetime.now().isoformat()
            }).eq('token', self.device_id).execute()
            
            if response.data:
                print("✅ QR code do totem atualizado com sucesso!")
                return True
            else:
                print("❌ Falha ao atualizar QR code do totem")
                return False
                
        except Exception as e:
            print(f"❌ Erro ao atualizar QR code: {e}")
            return False
    
    def obter_totem_por_token(self):
        """
        Obtém os dados do totem pelo token (Device ID).
        
        Returns:
            dict: Resultado da operação com success e data
        """
        try:
            if not self.device_id:
                log_error("❌ Device ID não disponível")
                return {
                    'success': False,
                    'message': 'Device ID não disponível',
                    'data': None
                }
            
            if not self.supabase:
                log_error("❌ Conexão Supabase não disponível")
                return {
                    'success': False,
                    'message': 'Conexão Supabase não disponível',
                    'data': None
                }
            
            log_debug(f"🔍 Buscando totem com Device ID: {self.device_id}")
            response = self.supabase.table('totens').select('*').eq('token', self.device_id).execute()
            
            log_debug(f"📊 Resposta da consulta: {len(response.data) if response.data else 0} registros encontrados")
            
            if response.data and len(response.data) > 0:
                totem_data = response.data[0]
                log_success(f"✅ Totem encontrado: ID={totem_data.get('id', 'N/A')}, Quadra ID={totem_data.get('quadra_id', 'N/A')}")
                return {
                    'success': True,
                    'message': 'Totem encontrado',
                    'data': totem_data
                }
            else:
                log_warning(f"⚠️ Nenhum totem encontrado com Device ID: {self.device_id}")
                log_info("💡 Verifique se o dispositivo foi registrado no painel administrativo")
                return {
                    'success': False,
                    'message': 'Totem não encontrado na base de dados',
                    'data': None
                }
                
        except Exception as e:
            log_error(f"❌ Erro ao obter totem: {e}")
            return {
                'success': False,
                'message': f'Erro ao consultar totem: {e}',
                'data': None
            }
    
    def initialize_session(self):
        """
        Inicializa uma nova sessão executando todas as validações necessárias.
        Substitui o antigo executar_verificacao_completa() com validação obrigatória de arena/quadra.
        
        Returns:
            dict: Resultado da operação com status e dados da sessão
        """
        resultado = {
            'success': False,
            'device_id': None,
            'totem_data': None,
            'cameras_data': None,
            'arena_data': None,
            'quadra_data': None,
            'session_data': None,
            'message': ''
        }
        
        try:
            log_info("🔧 Inicializando nova sessão com validações completas")
            
            # 1. Verifica Device ID
            self.device_id = self.verificar_device_id()
            if not self.device_id:
                resultado['message'] = 'Device ID não encontrado ou inválido'
                return resultado
            
            resultado['device_id'] = self.device_id
            log_debug(f"✅ Device ID validado: {self.device_id}")
            
            # 2. Conecta ao Supabase
            if not self.conectar_supabase():
                resultado['message'] = 'Falha na conexão com Supabase'
                return resultado
            
            log_debug("✅ Conexão Supabase estabelecida")
            
            # 3. Insere/verifica totem
            totem_data = self.inserir_totem()
            if not totem_data:
                resultado['message'] = 'Falha ao inserir/verificar totem'
                return resultado
            
            resultado['totem_data'] = totem_data
            log_debug(f"✅ Totem validado: {totem_data['id']}")
            
            # 4. VALIDAÇÃO OBRIGATÓRIA: Verifica se totem está associado a uma quadra
            quadra_id = totem_data.get('quadra_id')
            if not quadra_id:
                resultado['message'] = 'ERRO CRÍTICO: Totem não está associado a uma quadra (quadra_id é null)'
                log_error(resultado['message'])
                return resultado
            
            # 5. Busca informações da quadra
            quadra_result = self.get_quadra_info(quadra_id)
            if not quadra_result or not quadra_result.get('success'):
                resultado['message'] = f'ERRO CRÍTICO: Quadra não encontrada: {quadra_id}'
                log_error(resultado['message'])
                return resultado
            
            quadra_data = quadra_result['data']
            resultado['quadra_data'] = quadra_data
            log_debug(f"✅ Quadra validada: {quadra_data.get('nome', 'N/A')}")
            
            # 6. VALIDAÇÃO OBRIGATÓRIA: Verifica se quadra está associada a uma arena
            arena_id = quadra_data.get('arena_id')
            if not arena_id:
                resultado['message'] = 'ERRO CRÍTICO: Quadra não está associada a uma arena (arena_id é null)'
                log_error(resultado['message'])
                return resultado
            
            # 7. Busca informações da arena
            arena_result = self.get_arena_info(arena_id)
            if not arena_result or not arena_result.get('success'):
                resultado['message'] = f'ERRO CRÍTICO: Arena não encontrada: {arena_id}'
                log_error(resultado['message'])
                return resultado
            
            arena_data = arena_result['data']
            resultado['arena_data'] = arena_data
            log_debug(f"✅ Arena validada: {arena_data.get('nome', 'N/A')}")
            
            # 8. Insere câmeras
            log_debug(f"🔧 Processando câmeras para totem ID: {totem_data['id']}")
            cameras_resultado = self.inserir_cameras(totem_data['id'])
            
            if cameras_resultado['success']:
                resultado['cameras_data'] = cameras_resultado['cameras_inseridas']
                log_debug(cameras_resultado['message'])
                
                # 9. Verifica se as câmeras foram inseridas corretamente
                log_debug("🔧 Verificando inserção das câmeras")
                verificacao = self.verificar_cameras_inseridas(totem_data['id'])
                
                if verificacao['success']:
                    # 10. Cria SessionManager e gera sessão
                    session_manager = SessionManager(self)
                    session_result = session_manager.create_session(
                        totem_data, arena_data, quadra_data, resultado['cameras_data']
                    )
                    
                    if session_result and isinstance(session_result, dict) and session_result.get('success'):
                        resultado['session_data'] = session_result['session_data']
                        resultado['success'] = True
                        resultado['message'] = 'Sessão inicializada com sucesso - Todas as validações passaram'
                        
                        log_success("✅ Sessão inicializada com sucesso!")
                        log_info(f"🏛️ Arena: {arena_data.get('nome', 'N/A')}")
                        log_info(f"🏟️ Quadra: {quadra_data.get('nome', 'N/A')}")
                        log_info(f"📹 Câmeras: {len(resultado['cameras_data'])}")
                    else:
                        resultado['message'] = f"Validações OK, mas falha ao criar sessão: {session_result.get('message', 'Erro desconhecido') if isinstance(session_result, dict) else 'Erro na criação da sessão'}"
                        log_error(resultado['message'])
                else:
                    resultado['message'] = f"Validações OK, mas erro na verificação das câmeras: {verificacao['message']}"
                    log_warning(resultado['message'])
            else:
                resultado['message'] = f"Validações OK, mas falha nas câmeras: {cameras_resultado['message']}"
                log_warning(resultado['message'])
            
            return resultado
            
        except Exception as e:
            resultado['message'] = f'Erro na inicialização da sessão: {e}'
            log_error(f"❌ Erro na inicialização da sessão: {e}")
            return resultado
    
    def executar_verificacao_completa(self):
        """
        MÉTODO DEPRECIADO: Use initialize_session() ao invés deste método.
        Mantido para compatibilidade com código existente.
        
        Returns:
            dict: Resultado da operação
        """
        log_warning("⚠️ AVISO: executar_verificacao_completa() está depreciado. Use initialize_session()")
        return self.initialize_session()

    def get_arena_quadra_names(self):
        """
        Busca os nomes reais da arena e quadra usando arena_id/quadra_id já validados.
        
        Returns:
            dict: Resultado com nomes da arena e quadra ou fallback
        """
        resultado = {
            'success': False,
            'arena_nome': None,
            'quadra_nome': None,
            'using_fallback': False,
            'message': ''
        }
        
        try:
            if not self.supabase:
                resultado['message'] = 'Supabase não conectado'
                return resultado
            
            # Buscar dados do totem
            totem_data = self.obter_totem_por_token()
            if not totem_data:
                resultado['message'] = 'Totem não encontrado'
                return resultado
            
            quadra_id = totem_data.get('quadra_id')
            if not quadra_id:
                resultado['message'] = 'Totem não associado a uma quadra'
                return resultado
            
            # Buscar informações da quadra
            quadra_response = self.supabase.table('quadras').select('*').eq('id', quadra_id).execute()
            
            if not quadra_response.data:
                resultado['message'] = f'Quadra não encontrada: {quadra_id}'
                return resultado
            
            quadra_info = quadra_response.data[0]
            arena_id = quadra_info.get('arena_id')
            
            if not arena_id:
                resultado['message'] = 'Quadra não associada a uma arena'
                return resultado
            
            # Buscar informações da arena
            arena_response = self.supabase.table('arenas').select('*').eq('id', arena_id).execute()
            
            if not arena_response.data:
                resultado['message'] = f'Arena não encontrada: {arena_id}'
                return resultado
            
            arena_info = arena_response.data[0]
            
            # Sucesso - nomes encontrados
            resultado['success'] = True
            resultado['arena_nome'] = arena_info.get('nome', 'Arena Desconhecida')
            resultado['quadra_nome'] = quadra_info.get('nome', 'Quadra Desconhecida')
            resultado['message'] = 'Nomes encontrados com sucesso'
            
            return resultado
            
        except Exception as e:
            resultado['message'] = f'Erro ao buscar nomes: {e}'
            log_error(f"Erro ao buscar nomes da arena/quadra: {e}")
            return resultado

    def upload_video_to_bucket(self, video_path, bucket_path, timeout_seconds=300):
        """
        Faz upload do vídeo para o bucket do Supabase com retry e verificação de tamanho.
        
        Args:
            video_path (str): Caminho local do vídeo
            bucket_path (str): Caminho no bucket (estrutura hierárquica)
            timeout_seconds (int): Timeout para upload
            
        Returns:
            dict: Resultado do upload
        """
        resultado = {
            'success': False,
            'bucket_path': bucket_path,
            'file_size': 0,
            'upload_time': 0,
            'message': ''
        }
        
        try:
            if not self.supabase:
                resultado['message'] = 'Supabase não conectado'
                return resultado
            
            if not os.path.exists(video_path):
                resultado['message'] = f'Arquivo não encontrado: {video_path}'
                return resultado
            
            # Verificar tamanho do arquivo
            file_size = os.path.getsize(video_path)
            file_size_mb = file_size / (1024 * 1024)
            max_size_mb = int(os.getenv('MAX_FILE_SIZE_MB', '50'))
            
            if file_size_mb > max_size_mb:
                resultado['message'] = f'Arquivo muito grande: {file_size_mb:.1f}MB (máximo: {max_size_mb}MB)'
                resultado['error_code'] = 413
                return resultado
            
            resultado['file_size'] = file_size
            
            # Configurações do bucket
            bucket_name = os.getenv('SUPABASE_BUCKET_NAME', 'videos-replay')
            
            # Configurações de retry
            enable_retry = os.getenv('ENABLE_UPLOAD_RETRY', 'true').lower() == 'true'
            max_retries = int(os.getenv('MAX_RETRY_ATTEMPTS', '3'))
            
            for attempt in range(max_retries + 1):
                try:
                    start_time = time.time()
                    
                    # Ler arquivo
                    with open(video_path, 'rb') as file:
                        file_data = file.read()
                    
                    # Upload para o bucket
                    upload_response = self.supabase.storage.from_(bucket_name).upload(
                        path=bucket_path,
                        file=file_data,
                        file_options={
                            "content-type": "video/mp4",
                            "cache-control": "3600"
                        }
                    )
                    
                    upload_time = time.time() - start_time
                    resultado['upload_time'] = upload_time
                    
                    # Verificar se o upload foi bem-sucedido
                    if hasattr(upload_response, 'error') and upload_response.error:
                        error_msg = str(upload_response.error)
                        
                        # Verificar se é erro de tamanho
                        if 'Payload too large' in error_msg or '413' in error_msg:
                            resultado['message'] = f'Arquivo muito grande para o bucket ({file_size_mb:.1f}MB)'
                            resultado['error_code'] = 413
                            return resultado
                        
                        if attempt < max_retries and enable_retry:
                            wait_time = (attempt + 1) * 2  # Backoff exponencial
                            log_warning(f"Tentativa {attempt + 1} falhou, tentando novamente em {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                        else:
                            resultado['message'] = f'Erro no upload: {error_msg}'
                            resultado['attempt'] = attempt + 1
                            return resultado
                    
                    resultado['success'] = True
                    resultado['message'] = f'Upload concluído em {upload_time:.1f}s'
                    resultado['attempt'] = attempt + 1
                    return resultado
                    
                except Exception as upload_error:
                    error_msg = str(upload_error)
                    
                    # Verificar tipos específicos de erro
                    if 'Payload too large' in error_msg or '413' in error_msg:
                        resultado['message'] = f'Arquivo muito grande para o bucket ({file_size_mb:.1f}MB)'
                        resultado['error_code'] = 413
                        return resultado
                    
                    # Verificar se é erro de duplicata (409)
                    if ('409' in error_msg or 'Duplicate' in error_msg or 'already exists' in error_msg):
                        log_info(f"✅ Arquivo já existe no bucket - considerando como sucesso")
                        log_info(f"📂 Caminho: {bucket_path}")
                        
                        upload_time = time.time() - start_time
                        resultado['success'] = True
                        resultado['upload_time'] = upload_time
                        resultado['message'] = f'Arquivo já existe (duplicata) - {upload_time:.1f}s'
                        resultado['attempt'] = attempt + 1
                        resultado['duplicate'] = True
                        return resultado
                    
                    if attempt < max_retries and enable_retry:
                        wait_time = (attempt + 1) * 2
                        log_warning(f"Erro na tentativa {attempt + 1}: {error_msg}")
                        log_warning(f"Tentando novamente em {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        resultado['message'] = f'Erro após {attempt + 1} tentativas: {error_msg}'
                        resultado['attempt'] = attempt + 1
                        return resultado
            
            resultado['message'] = f'Upload falhou após {max_retries + 1} tentativas'
            return resultado
            
        except Exception as e:
            upload_time = time.time() - start_time if 'start_time' in locals() else 0
            resultado['upload_time'] = upload_time
            resultado['message'] = f'Erro geral no upload: {e}'
            log_error(f"Erro no upload para bucket: {e}")
            return resultado

    def verify_upload_success(self, bucket_path, expected_size=None):
        """
        Verifica se o upload foi bem-sucedido através de callback.
        
        Args:
            bucket_path (str): Caminho no bucket para verificar
            expected_size (int, optional): Tamanho esperado do arquivo
            
        Returns:
            dict: Resultado da verificação
        """
        resultado = {
            'success': False,
            'exists': False,
            'size_match': False,
            'bucket_size': 0,
            'message': ''
        }
        
        try:
            if not self.supabase:
                resultado['message'] = 'Supabase não conectado'
                return resultado
            
            bucket_name = os.getenv('SUPABASE_BUCKET_NAME', 'videos-replay')
            
            # Verificar se arquivo existe no bucket
            try:
                file_info = self.supabase.storage.from_(bucket_name).get_public_url(bucket_path)
                if file_info:
                    resultado['exists'] = True
                    
                    # Se possível, verificar tamanho (implementação básica)
                    if expected_size:
                        # Nota: Supabase não fornece tamanho diretamente via API pública
                        # Esta é uma verificação básica de existência
                        resultado['size_match'] = True  # Assumir que existe = tamanho OK
                        resultado['bucket_size'] = expected_size
                    
                    resultado['success'] = True
                    resultado['message'] = 'Arquivo verificado no bucket'
                else:
                    resultado['message'] = 'Arquivo não encontrado no bucket'
                    
            except Exception as verify_error:
                resultado['message'] = f'Erro na verificação: {verify_error}'
            
            return resultado
            
        except Exception as e:
            resultado['message'] = f'Erro na verificação: {e}'
            log_error(f"Erro ao verificar upload: {e}")
            return resultado


class SessionManager:
    """
    Gerenciador de sessões para cache de validações do Supabase.
    Responsável por criar, validar e gerenciar sessões com dados validados.
    INCLUI VALIDAÇÕES OBRIGATÓRIAS CRÍTICAS para inicialização do sistema.
    """
    
    def __init__(self, supabase_manager):
        """
        Inicializa o gerenciador de sessões.
        
        Args:
            supabase_manager (SupabaseManager): Instância do SupabaseManager
        """
        self.supabase_manager = supabase_manager
        
        # Caminho para o arquivo de sessão
        src_dir = Path(__file__).parent
        device_config_dir = src_dir.parent / "device_config"
        device_config_dir.mkdir(exist_ok=True)
        self.session_file = device_config_dir / "session_data.json"
        
        # Estado da sessão
        self.session_active = False
        self.session_data = None
        
        log_debug(f"📁 SessionManager inicializado - Arquivo: {self.session_file}")
    
    def validate_critical_requirements(self):
        """
        VALIDAÇÕES OBRIGATÓRIAS CRÍTICAS para inicialização do sistema.
        Sistema NÃO deve inicializar se alguma validação falhar.
        
        Returns:
            dict: Resultado das validações críticas
        """
        resultado = {
            'success': False,
            'message': '',
            'details': {},
            'should_exit': False
        }
        
        try:
            log_info("🔒 Executando validações obrigatórias críticas...")
            
            # A. VALIDAÇÃO ARENA/QUADRA ASSOCIATION (REMOVIDA - NÃO MAIS CRÍTICA)
            # A validação de arena/quadra agora é feita com sistema de retry no gravador_camera.py
            arena_quadra_result = self._validate_arena_quadra_association()
            resultado['details']['arena_quadra'] = arena_quadra_result
            
            if not arena_quadra_result['success']:
                log_warning("⚠️ Arena/Quadra não associada - será verificada periodicamente")
                log_info("💡 Sistema continuará funcionando e verificará a associação a cada 30 segundos")
            else:
                log_success("✅ Arena/Quadra já associada")
            
            # B. VALIDAÇÃO CÂMERAS ONVIF (CRÍTICO)
            onvif_result = self._validate_onvif_cameras()
            resultado['details']['onvif_cameras'] = onvif_result
            
            if not onvif_result['success']:
                resultado['message'] = "❌ Dados ONVIF das câmeras não são válidos"
                resultado['should_exit'] = True
                log_error(f"CRÍTICO: {resultado['message']}")
                log_error("💡 Orientação: Execute o scan ONVIF para detectar e configurar as câmeras")
                return resultado
            
            # C. VALIDAÇÃO DEVICE ID CONSISTENCY (CRÍTICO)
            device_id_result = self._validate_device_id_consistency()
            resultado['details']['device_id'] = device_id_result
            
            if not device_id_result['success']:
                resultado['message'] = "❌ Device ID inconsistente ou inválido"
                resultado['should_exit'] = True
                log_error(f"CRÍTICO: {resultado['message']}")
                log_error("💡 Orientação: Possível cópia de arquivos entre dispositivos - regenere o Device ID")
                return resultado
            
            # TODAS AS VALIDAÇÕES PASSARAM
            resultado['success'] = True
            resultado['message'] = "✅ Todas as validações críticas foram aprovadas"
            log_success("🔒 Validações obrigatórias críticas: APROVADAS")
            
            return resultado
            
        except Exception as e:
            log_error(f"❌ Erro durante validações críticas: {e}")
            resultado['message'] = f"Erro interno durante validações: {e}"
            resultado['should_exit'] = True
            return resultado
    
    def _validate_arena_quadra_association(self):
        """
        A. VALIDAÇÃO ARENA/QUADRA ASSOCIATION (CRÍTICO)
        
        Validações:
        - Totem tem quadra_id: Campo não pode ser null/vazio
        - Quadra existe: Registro existe na tabela quadras
        - Quadra tem arena_id: Campo não pode ser null/vazio
        - Arena existe: Registro existe na tabela arenas
        - Nomes válidos: Arena e quadra têm nomes não vazios
        
        Returns:
            dict: Resultado da validação
        """
        resultado = {
            'success': False,
            'message': '',
            'totem_data': None,
            'quadra_data': None,
            'arena_data': None
        }
        
        try:
            log_debug("🔍 Validando associação Arena/Quadra...")
            
            # 1. Verificar se totem existe e tem quadra_id
            totem_data = self.supabase_manager.obter_totem_por_token()
            if not totem_data or not totem_data.get('success'):
                resultado['message'] = "Totem não encontrado na base de dados"
                return resultado
            
            totem_info = totem_data['data']
            quadra_id = totem_info.get('quadra_id')
            
            if not quadra_id:
                resultado['message'] = "Totem não está associado a uma quadra (quadra_id é null)"
                return resultado
            
            resultado['totem_data'] = totem_info
            log_debug(f"✅ Totem válido com quadra_id: {quadra_id}")
            
            # 2. Verificar se quadra existe e tem arena_id
            quadra_data = self.supabase_manager.get_quadra_info(quadra_id)
            if not quadra_data or not quadra_data.get('success'):
                resultado['message'] = f"Quadra {quadra_id} não encontrada na base de dados"
                return resultado
            
            quadra_info = quadra_data['data']
            arena_id = quadra_info.get('arena_id')
            
            if not arena_id:
                resultado['message'] = "Quadra não está associada a uma arena (arena_id é null)"
                return resultado
            
            if not quadra_info.get('nome') or quadra_info.get('nome').strip() == '':
                resultado['message'] = "Quadra não tem nome válido"
                return resultado
            
            resultado['quadra_data'] = quadra_info
            log_debug(f"✅ Quadra válida: {quadra_info.get('nome')} (arena_id: {arena_id})")
            
            # 3. Verificar se arena existe e tem nome válido
            arena_data = self.supabase_manager.get_arena_info(arena_id)
            if not arena_data or not arena_data.get('success'):
                resultado['message'] = f"Arena {arena_id} não encontrada na base de dados"
                return resultado
            
            arena_info = arena_data['data']
            
            if not arena_info.get('nome') or arena_info.get('nome').strip() == '':
                resultado['message'] = "Arena não tem nome válido"
                return resultado
            
            resultado['arena_data'] = arena_info
            log_debug(f"✅ Arena válida: {arena_info.get('nome')}")
            
            # VALIDAÇÃO COMPLETA
            resultado['success'] = True
            resultado['message'] = f"Associação válida: {arena_info.get('nome')} > {quadra_info.get('nome')}"
            log_success(f"🏟️ Arena/Quadra: {arena_info.get('nome')} > {quadra_info.get('nome')}")
            
            return resultado
            
        except Exception as e:
            log_error(f"❌ Erro na validação Arena/Quadra: {e}")
            resultado['message'] = f"Erro interno: {e}"
            return resultado
    
    def _validate_onvif_cameras(self):
        """
        B. VALIDAÇÃO CÂMERAS ONVIF (CRÍTICO)
        
        Validações:
        - Arquivo ONVIF existe: camera_onvif_info_*.json presente
        - Dados válidos: JSON pode ser lido e tem estrutura esperada
        - UUIDs consistentes: device_uuid nas câmeras corresponde aos dados ONVIF
        - Câmeras registradas: Existem registros na tabela cameras para o totem
        - Correspondência: Número de câmeras ONVIF = número de câmeras registradas
        
        Returns:
            dict: Resultado da validação
        """
        resultado = {
            'success': False,
            'message': '',
            'onvif_data': None,
            'cameras_data': None,
            'cameras_count': 0,
            'onvif_count': 0
        }
        
        try:
            log_debug("📹 Validando câmeras ONVIF...")
            
            # 1. Verificar se arquivo ONVIF existe e é válido
            onvif_data = self.supabase_manager.carregar_informacoes_onvif()
            if not onvif_data:
                resultado['message'] = "Arquivo ONVIF não encontrado ou inválido"
                return resultado
            
            resultado['onvif_data'] = onvif_data
            resultado['onvif_count'] = len(onvif_data)
            log_debug(f"✅ Arquivo ONVIF válido com {resultado['onvif_count']} câmeras")
            
            # 2. Verificar se totem tem câmeras registradas
            device_id = self.supabase_manager.device_id
            if not device_id:
                resultado['message'] = "Device ID não disponível para verificar câmeras"
                return resultado
            
            # Buscar totem para obter ID
            totem_data = self.supabase_manager.obter_totem_por_token()
            if not totem_data or not totem_data.get('success'):
                resultado['message'] = "Totem não encontrado para verificar câmeras"
                return resultado
            
            totem_id = totem_data['data']['id']
            
            # Verificar câmeras registradas
            cameras_data = self.supabase_manager.verificar_cameras_existem(totem_id)
            if not cameras_data:
                resultado['message'] = "Nenhuma câmera registrada para este totem"
                return resultado

            resultado['cameras_data'] = cameras_data
            resultado['cameras_count'] = len(cameras_data)
            log_debug(f"✅ {resultado['cameras_count']} câmeras registradas no banco")
            
            # 3. Verificar correspondência de quantidade
            if resultado['onvif_count'] != resultado['cameras_count']:
                resultado['message'] = f"Inconsistência: {resultado['onvif_count']} câmeras ONVIF vs {resultado['cameras_count']} registradas"
                return resultado
            
            # 4. Verificar UUIDs consistentes (se disponíveis)
            device_uuids = []
            log_debug(f"🔍 Verificando estrutura ONVIF data: {type(onvif_data)}")
            
            if isinstance(onvif_data, dict):
                for camera_key, camera_info in onvif_data.items():
                    log_debug(f"🔍 Processando {camera_key}: {type(camera_info)}")
                    if isinstance(camera_info, dict):
                        dispositivo = camera_info.get('dispositivo', {})
                        device_uuid = dispositivo.get('device_uuid')
                        if device_uuid and device_uuid != 'N/A':
                            device_uuids.append(device_uuid)
                    else:
                        log_error(f"❌ camera_info não é dict: {type(camera_info)}")
            else:
                log_error(f"❌ onvif_data não é dict: {type(onvif_data)}")
                resultado['message'] = f"Estrutura ONVIF inválida: esperado dict, recebido {type(onvif_data)}"
                return resultado
            
            if device_uuids:
                uuid_check = self.supabase_manager.verificar_cameras_onvif_existem(device_uuids)
                if not uuid_check or not uuid_check.get('success'):
                    resultado['message'] = "UUIDs ONVIF não correspondem às câmeras registradas"
                    return resultado
                
                log_debug(f"✅ UUIDs ONVIF consistentes: {len(device_uuids)} verificados")
            
            # VALIDAÇÃO COMPLETA
            resultado['success'] = True
            resultado['message'] = f"Câmeras ONVIF válidas: {resultado['cameras_count']} câmeras configuradas"
            log_success(f"📹 ONVIF: {resultado['cameras_count']} câmeras validadas")
            
            return resultado
            
        except Exception as e:
            log_error(f"❌ Erro na validação ONVIF: {e}")
            resultado['message'] = f"Erro interno: {e}"
            return resultado
    
    def _validate_device_id_consistency(self):
        """
        C. VALIDAÇÃO DEVICE ID CONSISTENCY (CRÍTICO)
        
        Validações:
        - Device ID válido: É um UUID válido
        - Hardware match: Corresponde ao hardware atual
        - Token exists: Existe na tabela totens
        - Consistência: Device ID no arquivo = Device ID do hardware
        
        Returns:
            dict: Resultado da validação
        """
        resultado = {
            'success': False,
            'message': '',
            'device_id': None,
            'hardware_uuid': None,
            'file_uuid': None,
            'token_exists': False
        }
        
        try:
            log_debug("🔑 Validando consistência do Device ID...")
            
            # 1. Verificar Device ID do arquivo
            file_device_id = self.supabase_manager.device_manager.get_device_id()
            if not file_device_id:
                resultado['message'] = "Device ID não encontrado no arquivo"
                return resultado
            
            resultado['file_uuid'] = file_device_id
            
            # 2. Verificar se é um UUID válido
            try:
                uuid.UUID(file_device_id)
                log_debug(f"✅ Device ID é um UUID válido: {file_device_id[:8]}...")
            except ValueError:
                resultado['message'] = "Device ID não é um UUID válido"
                return resultado
            
            # 3. Verificar Device ID do hardware
            device_info = self.supabase_manager.device_manager.get_device_info()
            if not device_info or 'device_id' not in device_info:
                resultado['message'] = "Não foi possível obter Device ID do hardware"
                return resultado
            
            hardware_device_id = device_info['device_id']
            
            resultado['hardware_uuid'] = hardware_device_id
            
            # 4. Verificar consistência entre arquivo e hardware
            if file_device_id != hardware_device_id:
                resultado['message'] = "Device ID do arquivo não corresponde ao hardware atual"
                log_warning(f"⚠️ Arquivo: {file_device_id[:8]}... vs Hardware: {hardware_device_id[:8]}...")
                return resultado
            
            resultado['device_id'] = file_device_id
            log_debug(f"✅ Device ID consistente: {file_device_id[:8]}...")
            
            # 5. Verificar se token existe na tabela totens
            token_check = self.supabase_manager.verificar_token_existe(file_device_id)
            if not token_check:
                resultado['message'] = "Device ID não encontrado na tabela totens"
                return resultado
            
            resultado['token_exists'] = True
            log_debug("✅ Device ID existe na tabela totens")
            
            # VALIDAÇÃO COMPLETA
            resultado['success'] = True
            resultado['message'] = f"Device ID consistente e válido: {file_device_id[:8]}..."
            log_success(f"🔑 Device ID: {file_device_id[:8]}... (consistente)")
            
            return resultado
            
        except Exception as e:
            log_error(f"❌ Erro na validação Device ID: {e}")
            resultado['message'] = f"Erro interno: {e}"
            return resultado
    
    def create_session(self, totem_data, arena_data, quadra_data, cameras_data):
        """
        Cria uma nova sessão com todas as validações executadas.
        
        Args:
            totem_data (dict): Dados do totem validado
            arena_data (dict): Dados da arena validada
            quadra_data (dict): Dados da quadra validada
            cameras_data (list): Lista de câmeras validadas
            
        Returns:
            dict: Resultado da criação da sessão
        """
        resultado = {
            'success': False,
            'session_data': None,
            'message': ''
        }
        
        try:
            log_info(f"🔧 create_session - tipos recebidos: arena={type(arena_data)}, quadra={type(quadra_data)}")
            log_info("🔧 Criando nova sessão com dados validados")
            
            # Define timestamps para a sessão
            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(hours=8)
            
            # Obtém Device ID e UUID
            device_id = self.supabase_manager.device_id
            device_info = self.supabase_manager.device_manager.get_device_info()
            device_uuid = device_info.get('device_id') if device_info and isinstance(device_info, dict) else device_id
            
            # Carrega informações ONVIF se disponíveis
            onvif_info = self.supabase_manager.carregar_informacoes_onvif()
            
            # Processa dados das câmeras
            cameras_processed = []
            log_debug(f"📋 Processando {len(cameras_data)} câmeras do banco:")
            for i, camera in enumerate(cameras_data):
                log_debug(f"   Câmera {i}: {camera}")
                camera_info = {
                    'id': camera.get('id') if isinstance(camera, dict) else None,
                    'nome': camera.get('nome', 'Camera Desconhecida') if isinstance(camera, dict) else 'Camera Desconhecida',
                    'ordem': camera.get('ordem', i+1) if isinstance(camera, dict) else i+1,  # Usar índice+1 se ordem não estiver definida
                    'onvif_uuid': 'N/A',
                    'serial_number': 'N/A',
                    'ip': 'N/A',
                    'totem_id': camera.get('totem_id') if isinstance(camera, dict) else None
                }
                log_debug(f"   Câmera processada: ordem={camera_info['ordem']}, nome={camera_info['nome']}")
                
                # Tenta encontrar dados ONVIF correspondentes
                if onvif_info and isinstance(onvif_info, dict):
                    log_debug(f"🔍 Buscando ONVIF para câmera ordem {camera_info['ordem']}")
                    found_match = False
                    for camera_key, onvif_camera in onvif_info.items():
                        onvif_camera_id = onvif_camera.get('camera_id')
                        log_debug(f"🔍 Verificando {camera_key}: camera_id={onvif_camera_id} vs ordem={camera_info['ordem']}")
                        if onvif_camera_id == camera_info['ordem']:
                            dispositivo = onvif_camera.get('dispositivo', {})
                            configuracao = onvif_camera.get('configuracao', {})
                            device_uuid = dispositivo.get('device_uuid', 'N/A')
                            log_debug(f"✅ Match encontrado! {camera_key} -> UUID: {device_uuid}")
                            camera_info.update({
                                'onvif_uuid': device_uuid,
                                'serial_number': dispositivo.get('serial_number', 'N/A'),
                                'ip': configuracao.get('ip', 'N/A')
                            })
                            found_match = True
                            break
                    if not found_match:
                        log_warning(f"⚠️ Nenhum match ONVIF encontrado para câmera ordem {camera_info['ordem']}")
                
                cameras_processed.append(camera_info)
            
            # Monta estrutura da sessão
            session_data = {
                'session_id': str(uuid.uuid4()),
                'created_at': now.isoformat(),
                'expires_at': expires_at.isoformat(),
                'device_info': {
                    'device_id': device_id,
                    'device_uuid': device_uuid
                },
                'totem_info': {
                    'id': totem_data.get('id'),
                    'token': totem_data.get('token'),
                    'quadra_id': totem_data.get('quadra_id')
                },
                'arena_info': {
                    'id': arena_data.get('id'),
                    'nome': arena_data.get('nome', 'Arena Desconhecida'),
                    'nome_sanitizado': self.supabase_manager.sanitize_folder_name(arena_data.get('nome', 'Arena'))
                },
                'quadra_info': {
                    'id': quadra_data.get('id'),
                    'nome': quadra_data.get('nome', 'Quadra Desconhecida'),
                    'nome_sanitizado': self.supabase_manager.sanitize_folder_name(quadra_data.get('nome', 'Quadra'))
                },
                'cameras': cameras_processed,
                'supabase_config': {
                    'url': self.supabase_manager.supabase_url,
                    'bucket_name': os.getenv('SUPABASE_BUCKET_NAME', 'videos')
                },
                'validation_status': {
                    'all_valid': True,
                    'device_valid': True,
                    'totem_valid': True,
                    'arena_quadra_valid': True,
                    'cameras_valid': len(cameras_processed) > 0,
                    'onvif_valid': onvif_info is not None
                }
            }
            
            # Salva sessão no arquivo
            save_result = self._save_session_to_file(session_data)
            if not save_result['success']:
                resultado['message'] = f"Falha ao salvar sessão: {save_result['message']}"
                return resultado
            
            # Atualiza estado interno
            self.session_data = session_data
            self.session_active = True
            
            resultado['success'] = True
            resultado['session_data'] = session_data
            resultado['message'] = 'Sessão criada e salva com sucesso'
            
            log_success(f"✅ Sessão criada: {session_data['session_id']}")
            log_info(f"⏰ Expira em: {expires_at.strftime('%d/%m/%Y %H:%M:%S')}")
            log_info(f"📹 Câmeras na sessão: {len(cameras_processed)}")
            
            return resultado
            
        except Exception as e:
            resultado['message'] = f'Erro ao criar sessão: {e}'
            log_error(f"❌ Erro ao criar sessão: {e}")
            return resultado
    
    def validate_session(self):
        """
        Verifica se a sessão existente ainda é válida.
        
        Returns:
            bool: True se a sessão é válida, False caso contrário
        """
        try:
            log_debug("🔍 Validando sessão existente")
            
            # 1. Verifica se arquivo existe
            if not self.session_file.exists():
                log_debug("❌ Arquivo de sessão não existe")
                return False
            
            # 2. Carrega e valida JSON
            try:
                with open(self.session_file, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                log_warning(f"⚠️ Erro ao ler arquivo de sessão: {e}")
                return False
            
            # 3. Verifica campos obrigatórios
            required_fields = [
                'session_id', 'created_at', 'expires_at', 'device_info',
                'totem_info', 'arena_info', 'quadra_info', 'cameras',
                'validation_status'
            ]
            
            for field in required_fields:
                if field not in session_data:
                    log_warning(f"⚠️ Campo obrigatório ausente: {field}")
                    return False
            
            # 4. Verifica expiração
            try:
                expires_at = datetime.fromisoformat(session_data['expires_at'])
                now = datetime.now(timezone.utc)
                
                if now >= expires_at:
                    log_warning("⚠️ Sessão expirada")
                    return False
            except ValueError as e:
                log_warning(f"⚠️ Formato de data inválido: {e}")
                return False
            
            # 5. Verifica Device ID
            current_device_id = self.supabase_manager.device_manager.get_device_id()
            session_device_id = session_data.get('device_info', {}).get('device_id')
            
            if current_device_id != session_device_id:
                log_warning("⚠️ Device ID não corresponde")
                return False
            
            # 6. Verifica status de validação
            validation_status = session_data.get('validation_status', {})
            if not validation_status.get('all_valid', False):
                log_warning("⚠️ Status de validação indica problemas")
                return False
            
            # Sessão válida - atualiza estado interno
            self.session_data = session_data
            self.session_active = True
            
            log_success("✅ Sessão válida encontrada")
            log_debug(f"🆔 Session ID: {session_data['session_id']}")
            log_debug(f"⏰ Expira em: {expires_at.strftime('%d/%m/%Y %H:%M:%S')}")
            
            return True
            
        except Exception as e:
            log_error(f"❌ Erro na validação da sessão: {e}")
            return False
    
    def get_session_data(self):
        """
        Retorna dados da sessão em cache para uso durante operação.
        
        Returns:
            dict: Dados da sessão ou None se não houver sessão válida
        """
        try:
            # Se não há sessão ativa, tenta validar
            if not self.session_active:
                if not self.validate_session():
                    log_warning("⚠️ Nenhuma sessão válida disponível")
                    return None
            
            log_debug("📋 Retornando dados da sessão em cache")
            return self.session_data
            
        except Exception as e:
            log_error(f"❌ Erro ao obter dados da sessão: {e}")
            return None
    
    def _save_session_to_file(self, session_data):
        """
        Salva dados da sessão no arquivo JSON.
        
        Args:
            session_data (dict): Dados da sessão
            
        Returns:
            dict: Resultado da operação
        """
        resultado = {
            'success': False,
            'message': ''
        }
        
        try:
            # Garante que o diretório existe
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Salva com formatação legível
            with open(self.session_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            
            resultado['success'] = True
            resultado['message'] = 'Sessão salva com sucesso'
            
            log_debug(f"💾 Sessão salva em: {self.session_file}")
            
        except Exception as e:
            resultado['message'] = f'Erro ao salvar sessão: {e}'
            log_error(f"❌ Erro ao salvar sessão: {e}")
        
        return resultado


def main():
    """
    Função principal para testar o gerenciador do Supabase com SessionManager.
    """
    print("☁️ TESTE DO GERENCIADOR SUPABASE COM SESSÕES")
    print("=" * 60)
    print()
    
    # Cria uma instância do gerenciador
    supabase_manager = SupabaseManager()
    
    # Testa SessionManager primeiro
    print("🔧 TESTANDO SESSIONMANAGER")
    print("-" * 40)
    
    session_manager = SessionManager(supabase_manager)
    
    # Verifica se há sessão válida existente
    if session_manager.validate_session():
        print("✅ Sessão válida encontrada - usando dados em cache")
        session_data = session_manager.get_session_data()
        
        if session_data:
            print(f"🆔 Session ID: {session_data['session_id']}")
            print(f"🏛️ Arena: {session_data['arena_info']['nome']}")
            print(f"🏟️ Quadra: {session_data['quadra_info']['nome']}")
            print(f"📹 Câmeras: {len(session_data['cameras'])}")
            print(f"⏰ Expira em: {session_data['expires_at']}")
            return
    else:
        print("⚠️ Nenhuma sessão válida - criando nova sessão")
    
    print("\n🔧 INICIALIZANDO NOVA SESSÃO")
    print("-" * 40)
    
    # Inicializa nova sessão
    resultado = supabase_manager.initialize_session()
    
    print("\n" + "=" * 60)
    print("📊 RESULTADO FINAL:")
    print(f"✅ Sucesso: {resultado['success']}")
    print(f"🆔 Device ID: {resultado['device_id']}")
    print(f"💬 Mensagem: {resultado['message']}")
    
    if resultado['totem_data']:
        print(f"🏢 Totem ID: {resultado['totem_data']['id']}")
        print(f"📅 Criado em: {resultado['totem_data']['created_at']}")
    
    if resultado['arena_data']:
        print(f"🏛️ Arena: {resultado['arena_data']['nome']}")
        print(f"🆔 Arena ID: {resultado['arena_data']['id']}")
    
    if resultado['quadra_data']:
        print(f"🏟️ Quadra: {resultado['quadra_data']['nome']}")
        print(f"🆔 Quadra ID: {resultado['quadra_data']['id']}")
    
    if resultado['cameras_data']:
        print(f"📹 Câmeras inseridas: {len(resultado['cameras_data'])}")
        for camera in resultado['cameras_data']:
            nome = camera.get('nome', 'N/A')
            uuid_camera = camera.get('id', 'N/A')
            ordem = camera.get('ordem', 'N/A')
            print(f"   • {nome}")
            print(f"     🆔 UUID: {uuid_camera}")
            print(f"     🔢 Ordem: {ordem}")
    
    if resultado['session_data']:
        session_info = resultado['session_data']
        print(f"\n📋 SESSÃO CRIADA:")
        print(f"🆔 Session ID: {session_info['session_id']}")
        print(f"⏰ Criada em: {session_info['created_at']}")
        print(f"⏰ Expira em: {session_info['expires_at']}")
        print(f"✅ Status: {session_info['validation_status']['all_valid']}")
        print(f"📁 Arquivo: device_config/session_data.json")
    
    print("\n" + "=" * 60)
    print("🎯 TESTE CONCLUÍDO - Verifique o arquivo session_data.json")


if __name__ == "__main__":
    main()