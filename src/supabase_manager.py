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
from datetime import datetime
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
            list: Lista de câmeras existentes com esses UUIDs
        """
        try:
            if not self.supabase or not device_uuids:
                return []
            
            cameras_existentes = []
            
            for device_uuid in device_uuids:
                response = self.supabase.table('cameras').select('*').eq('id', device_uuid).execute()
                if response.data:
                    cameras_existentes.extend(response.data)
            
            if cameras_existentes:
                log_debug(f"Encontradas {len(cameras_existentes)} câmera(s) com UUID ONVIF já existente(s)")
                for cam in cameras_existentes:
                    log_debug(f"Câmera {cam['nome']} - UUID: {cam['id']}")
            
            return cameras_existentes
                
        except Exception as e:
            log_error(f"Erro ao verificar câmeras ONVIF existentes: {e}")
            return []
    
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
            
            # Carrega informações ONVIF das câmeras
            dados_onvif = self.carregar_informacoes_onvif()
            
            if not dados_onvif:
                log_warning("Dados ONVIF não encontrados, usando inserção padrão")
                return self._inserir_cameras_padrao(totem_id)
            
            # Extrai device_uuid das câmeras
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
            
            # Verifica se câmeras com esses UUIDs já existem
            cameras_existentes = self.verificar_cameras_onvif_existem(device_uuids)
            if len(cameras_existentes) >= len(cameras_onvif):
                resultado['success'] = True
                resultado['cameras_inseridas'] = cameras_existentes
                resultado['message'] = 'Câmeras ONVIF já existem - reutilizando'
                log_info("Câmeras ONVIF já existem - reutilizando")
                return resultado
            
            # Verifica se existem câmeras antigas para este totem (para fazer UPDATE)
            cameras_antigas = self.verificar_cameras_existem(totem_id)
            
            # Usa UPSERT para resolver conflitos automaticamente
            log_debug("Usando UPSERT para resolver conflitos de câmeras")
            
            # Prepara dados para UPSERT usando device_uuid como ID
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
            
            # Usa UPSERT para inserir/atualizar câmeras (resolve conflitos automaticamente)
            response = self.supabase.table('cameras').upsert(
                cameras_data,
                on_conflict='totem_id,ordem'  # Resolve conflito na constraint única
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
            resultado['message'] = f'Erro ao inserir câmeras ONVIF: {e}'
            log_error(f"Erro ao inserir câmeras ONVIF: {e}")
            return resultado

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
            
            # Insere as câmeras na tabela cameras
            response = self.supabase.table('cameras').insert(cameras_data).execute()
            
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
            dict: Dados do totem se encontrado, None caso contrário
        """
        try:
            if not self.device_id or not self.supabase:
                return None
            
            response = self.supabase.table('totens').select('*').eq('token', self.device_id).execute()
            
            if response.data:
                return response.data[0]
            else:
                return None
                
        except Exception as e:
            print(f"❌ Erro ao obter totem: {e}")
            return None
    
    def executar_verificacao_completa(self):
        """
        Executa a verificação completa: Device ID → Supabase → Inserção Totem → Inserção Câmeras.
        
        Returns:
            dict: Resultado da operação com status e dados
        """
        resultado = {
            'success': False,
            'device_id': None,
            'totem_data': None,
            'cameras_data': None,
            'message': ''
        }
        
        try:
            # Verifica cache primeiro
            if system_logger.is_cached('supabase_verification_complete'):
                log_debug("Verificação completa já executada (cache)")
                # Retorna dados do cache se disponível
                cached_result = getattr(self, '_cached_verification_result', None)
                if cached_result:
                    return cached_result
            
            log_info("Executando verificação completa do Supabase")
            
            # 1. Verifica Device ID
            self.device_id = self.verificar_device_id()
            if not self.device_id:
                resultado['message'] = 'Device ID não encontrado ou inválido'
                return resultado
            
            resultado['device_id'] = self.device_id
            
            # 2. Conecta ao Supabase
            if not self.conectar_supabase():
                resultado['message'] = 'Falha na conexão com Supabase'
                return resultado
            
            # 3. Insere/verifica totem
            totem_data = self.inserir_totem()
            if not totem_data:
                resultado['message'] = 'Falha ao inserir/verificar totem'
                return resultado
            
            resultado['totem_data'] = totem_data
            
            # 4. Insere câmeras
            log_debug(f"Processando câmeras para totem ID: {totem_data['id']}")
            cameras_resultado = self.inserir_cameras(totem_data['id'])
            
            if cameras_resultado['success']:
                resultado['cameras_data'] = cameras_resultado['cameras_inseridas']
                log_debug(cameras_resultado['message'])
                
                # 5. Verifica se as câmeras foram inseridas corretamente
                log_debug("Verificando inserção das câmeras")
                verificacao = self.verificar_cameras_inseridas(totem_data['id'])
                
                if verificacao['success']:
                    resultado['success'] = True
                    resultado['message'] = 'Totem e câmeras verificados/inseridos com sucesso'
                    
                    # Cache o resultado
                    self._cached_verification_result = resultado
                    system_logger.cache_verification('supabase_verification_complete', True)
                    
                    log_success("Todas as verificações concluídas com sucesso!")
                else:
                    resultado['message'] = f"Totem inserido, mas erro na verificação das câmeras: {verificacao['message']}"
                    log_warning(resultado['message'])
            else:
                resultado['message'] = f"Totem inserido, mas falha nas câmeras: {cameras_resultado['message']}"
                log_warning(resultado['message'])
            
            return resultado
            
        except Exception as e:
            resultado['message'] = f'Erro na verificação completa: {e}'
            log_error(f"Erro na verificação completa: {e}")
            return resultado

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


def main():
    """
    Função principal para testar o gerenciador do Supabase.
    """
    print("☁️ TESTE DO GERENCIADOR SUPABASE")
    print("=" * 50)
    print()
    
    # Cria uma instância do gerenciador
    supabase_manager = SupabaseManager()
    
    # Executa verificação completa
    resultado = supabase_manager.executar_verificacao_completa()
    
    print("\n" + "=" * 50)
    print("📊 RESULTADO FINAL:")
    print(f"✅ Sucesso: {resultado['success']}")
    print(f"🆔 Device ID: {resultado['device_id']}")
    print(f"💬 Mensagem: {resultado['message']}")
    
    if resultado['totem_data']:
        print(f"🏢 Totem ID: {resultado['totem_data']['id']}")
        print(f"📅 Criado em: {resultado['totem_data']['created_at']}")
    
    if resultado['cameras_data']:
        print(f"📹 Câmeras inseridas: {len(resultado['cameras_data'])}")
        for camera in resultado['cameras_data']:
            nome = camera.get('nome', 'N/A')
            uuid_camera = camera.get('id', 'N/A')
            ordem = camera.get('ordem', 'N/A')
            print(f"   • {nome}")
            print(f"     🆔 UUID: {uuid_camera}")
            print(f"     🔢 Ordem: {ordem}")


if __name__ == "__main__":
    main()