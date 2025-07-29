#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para obter informações de dispositivos de câmeras IP usando protocolo ONVIF
Especificamente para câmeras Motorola IP DOME 2MP modelo MTIDM022603
"""

import os
import sys
from urllib.parse import urlparse
import json
import uuid
from datetime import datetime
from pathlib import Path
import glob

try:
    from onvif import ONVIFCamera
    from onvif.exceptions import ONVIFError
except ImportError:
    print("❌ Biblioteca ONVIF não encontrada!")
    print("   Instale com: pip install onvif-zeep")
    sys.exit(1)

class ONVIFDeviceManager:
    """
    Gerenciador de informações ONVIF para câmeras IP
    """
    
    def __init__(self):
        # Pasta device_config na raiz do projeto (pasta pai da src)
        self.device_config_dir = Path(__file__).parent.parent / "device_config"
        self.device_config_dir.mkdir(exist_ok=True)
        
    def verificar_arquivo_existente(self):
        """
        Verifica se já existe um arquivo camera_onvif_info_*.json na pasta device_config
        
        Returns:
            dict: Informações sobre arquivo existente ou None se não existe
        """
        try:
            # Procura por arquivos com padrão camera_onvif_info_*.json
            pattern = str(self.device_config_dir / "camera_onvif_info_*.json")
            arquivos_existentes = glob.glob(pattern)
            
            if arquivos_existentes:
                # Pega o arquivo mais recente
                arquivo_mais_recente = max(arquivos_existentes, key=os.path.getctime)
                arquivo_path = Path(arquivo_mais_recente)
                
                # Carrega e valida o conteúdo
                try:
                    with open(arquivo_path, 'r', encoding='utf-8') as f:
                        dados = json.load(f)
                    
                    # Verifica se tem pelo menos uma câmera com UUID válido
                    cameras_validas = 0
                    for camera_key, camera_data in dados.items():
                        if camera_key.startswith('camera_') and isinstance(camera_data, dict):
                            dispositivo = camera_data.get('dispositivo', {})
                            if dispositivo.get('device_uuid') and dispositivo.get('device_uuid') != 'N/A':
                                cameras_validas += 1
                    
                    if cameras_validas > 0:
                        print(f"📋 Arquivo ONVIF existente encontrado: {arquivo_path.name}")
                        print(f"   📅 Criado em: {datetime.fromtimestamp(arquivo_path.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S')}")
                        print(f"   📹 Câmeras válidas: {cameras_validas}")
                        
                        return {
                            'existe': True,
                            'arquivo': arquivo_path,
                            'dados': dados,
                            'cameras_validas': cameras_validas,
                            'timestamp_criacao': arquivo_path.stat().st_ctime
                        }
                    else:
                        print(f"⚠️ Arquivo encontrado mas sem câmeras válidas: {arquivo_path.name}")
                        return None
                        
                except (json.JSONDecodeError, Exception) as e:
                    print(f"❌ Erro ao ler arquivo existente {arquivo_path.name}: {e}")
                    return None
            else:
                print("📋 Nenhum arquivo ONVIF existente encontrado")
                return None
                
        except Exception as e:
            print(f"❌ Erro ao verificar arquivos existentes: {e}")
            return None
    
    def obter_informacoes_cameras(self, force_recreate=False):
        """
        Obtém informações das câmeras, reutilizando arquivo existente se possível
        
        Args:
            force_recreate (bool): Força recriação mesmo se arquivo existir
            
        Returns:
            dict: Informações das câmeras ou None se falhou
        """
        print("\n🎥 === VERIFICAÇÃO DE INFORMAÇÕES ONVIF DAS CÂMERAS ===")
        print("-" * 60)
        
        # Verifica arquivo existente
        if not force_recreate:
            arquivo_existente = self.verificar_arquivo_existente()
            if arquivo_existente:
                print("✅ Reutilizando informações ONVIF existentes (sem recriar)")
                return arquivo_existente['dados']
        
        # Se chegou aqui, precisa criar novo arquivo
        print("🔄 Criando novo arquivo de informações ONVIF...")
        return self._executar_scan_completo()
    
    def _executar_scan_completo(self):
        """
        Executa o scan completo das câmeras ONVIF
        
        Returns:
            dict: Informações das câmeras
        """
        # Carrega configurações
        config = self._carregar_configuracoes()
        if not config:
            return None
        
        # Identifica as câmeras
        cameras_config = self._identificar_cameras(config)
        if not cameras_config:
            print("❌ Nenhuma câmera encontrada no arquivo de configuração!")
            return None
        
        print(f"📹 Encontradas {len(cameras_config)} câmera(s) configurada(s):")
        for cam in cameras_config:
            print(f"   - Câmera {cam['id']}: {cam['ip']} ({cam['usuario']})")
        
        print("\n" + "="*60 + "\n")
        
        # Conecta e obtém informações de cada câmera
        informacoes_cameras = {}
        
        for cam in cameras_config:
            print(f"🔍 PROCESSANDO CÂMERA {cam['id']} - {cam['ip']}")
            print("-" * 50)
            
            camera, device_service = self._conectar_camera_onvif(
                cam['ip'], cam['porta'], cam['usuario'], cam['senha']
            )
            
            if camera and device_service:
                informacoes = self._obter_informacoes_dispositivo(camera, device_service, cam['ip'])
                # Organiza informações de forma mais estruturada
                camera_info = {
                    'camera_id': cam['id'],
                    'configuracao': {
                        'ip': cam['ip'],
                        'rtsp_url': cam['rtsp_url'],
                        'usuario': cam['usuario']
                    },
                    'dispositivo': {
                        'fabricante': informacoes.get('fabricante', 'N/A'),
                        'modelo': informacoes.get('modelo', 'N/A'),
                        'serial_number': informacoes.get('serial_number', 'N/A'),
                        'device_uuid': informacoes.get('device_uuid', 'N/A'),
                        'firmware_version': informacoes.get('firmware_version', 'N/A'),
                        'hardware_id': informacoes.get('hardware_id', 'N/A')
                    },
                    'conexao': {
                        'status': informacoes.get('status_conexao', 'desconhecido'),
                        'timestamp': informacoes.get('timestamp', 'N/A'),
                        'capacidades': informacoes.get('capacidades', {}),
                        'rede': informacoes.get('rede', {}),
                        'horario_sistema': informacoes.get('horario_sistema', {})
                    }
                }
                informacoes_cameras[f"camera_{cam['id']}"] = camera_info
            else:
                informacoes_cameras[f"camera_{cam['id']}"] = {
                    'camera_id': cam['id'],
                    'configuracao': {
                        'ip': cam['ip'],
                        'rtsp_url': cam['rtsp_url'],
                        'usuario': cam['usuario']
                    },
                    'dispositivo': {
                        'fabricante': 'N/A',
                        'modelo': 'N/A',
                        'serial_number': 'N/A',
                        'device_uuid': 'N/A',
                        'firmware_version': 'N/A',
                        'hardware_id': 'N/A'
                    },
                    'conexao': {
                        'status': 'falha_conexao',
                        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'erro': 'Não foi possível conectar via ONVIF'
                    }
                }
            
            print("\n" + "="*60 + "\n")
        
        # Salva as informações
        arquivo_salvo = self._salvar_informacoes(informacoes_cameras)
        
        # Resumo final
        self._exibir_resumo_final(informacoes_cameras, arquivo_salvo)
        
        return informacoes_cameras
    
    def _gerar_uuid_dispositivo(self, serial_number, fabricante="Motorola", modelo="MTIDM022603"):
        """Gera um UUID baseado no serial number do dispositivo"""
        try:
            # Namespace personalizado para câmeras Motorola
            namespace_motorola = uuid.uuid5(uuid.NAMESPACE_DNS, f"{fabricante.lower()}.cameras.{modelo.lower()}")
            
            # Gera UUID determinístico baseado no serial
            device_uuid = uuid.uuid5(namespace_motorola, serial_number)
            
            return str(device_uuid)
            
        except Exception as e:
            print(f"⚠️  Erro ao gerar UUID: {e}")
            # Fallback: gera UUID baseado apenas no serial
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, serial_number))

    def _carregar_configuracoes(self):
        """Carrega as configurações do arquivo config.env"""
        config = {}
        config_path = Path(__file__).parent.parent / "config.env"
        
        if not config_path.exists():
            print(f"❌ Arquivo de configuração não encontrado: {config_path}")
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                if linha and not linha.startswith('#') and '=' in linha:
                    chave, valor = linha.split('=', 1)
                    config[chave.strip()] = valor.strip()
        
        return config

    def _identificar_cameras(self, config):
        """Identifica as câmeras configuradas"""
        cameras_config = []
        for i in range(1, 10):  # Procura até CAMERA_10
            camera_key = f'IP_CAMERA_{i}'
            if camera_key in config:
                rtsp_url = config[camera_key]
                ip, porta, usuario, senha = self._extrair_credenciais_rtsp(rtsp_url)
                
                if ip:
                    cameras_config.append({
                        'id': i,
                        'ip': ip,
                        'porta': porta,
                        'usuario': usuario,
                        'senha': senha,
                        'rtsp_url': rtsp_url
                    })
        return cameras_config

    def _extrair_credenciais_rtsp(self, rtsp_url):
        """Extrai IP, usuário e senha da URL RTSP"""
        try:
            parsed = urlparse(rtsp_url)
            ip = parsed.hostname
            porta = parsed.port or 554
            usuario = parsed.username or 'admin'
            senha = parsed.password or ''
            return ip, porta, usuario, senha
        except Exception as e:
            print(f"❌ Erro ao analisar URL RTSP: {e}")
            return None, None, None, None

    def _conectar_camera_onvif(self, ip, porta, usuario, senha):
        """Conecta na câmera usando ONVIF"""
        try:
            print(f"🔄 Conectando na câmera {ip}:{porta}...")
            
            # Tenta conectar na câmera ONVIF (porta padrão 80)
            camera = ONVIFCamera(ip, 80, usuario, senha)
            
            # Testa a conexão
            device_service = camera.devicemgmt
            device_info = device_service.GetDeviceInformation()
            
            print(f"✅ Conexão ONVIF estabelecida com {ip}")
            return camera, device_service
            
        except ONVIFError as e:
            print(f"❌ Erro ONVIF ao conectar em {ip}: {e}")
        except Exception as e:
            print(f"❌ Erro geral ao conectar em {ip}: {e}")
            
        return None, None

    def _obter_informacoes_dispositivo(self, camera, device_service, ip):
        """Obtém informações completas do dispositivo"""
        informacoes = {
            'ip': ip,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'status_conexao': 'conectado'
        }
        
        try:
            # Informações básicas do dispositivo
            print(f"📋 Obtendo informações do dispositivo {ip}...")
            device_info = device_service.GetDeviceInformation()
            
            # Gera UUID baseado no serial number
            device_uuid = self._gerar_uuid_dispositivo(
                device_info.SerialNumber, 
                device_info.Manufacturer, 
                device_info.Model
            )
            
            informacoes.update({
                'fabricante': device_info.Manufacturer,
                'modelo': device_info.Model,
                'firmware_version': device_info.FirmwareVersion,
                'serial_number': device_info.SerialNumber,
                'hardware_id': device_info.HardwareId,
                'device_uuid': device_uuid
            })
            
            print(f"   📱 Fabricante: {device_info.Manufacturer}")
            print(f"   🏷️  Modelo: {device_info.Model}")
            print(f"   🔢 Número de Série: {device_info.SerialNumber}")
            print(f"   🆔 Device UUID: {device_uuid}")
            print(f"   💾 Firmware: {device_info.FirmwareVersion}")
            print(f"   🔧 Hardware ID: {device_info.HardwareId}")
            
        except Exception as e:
            print(f"   ❌ Erro ao obter informações básicas: {e}")
            informacoes['erro_info_basicas'] = str(e)
        
        # Resto das informações (capacidades, rede, horário) - mantido igual
        try:
            # Capacidades essenciais do dispositivo
            print(f"   🔍 Obtendo capacidades...")
            capabilities = device_service.GetCapabilities()
            
            informacoes['capacidades'] = {
                'onvif_service_url': 'N/A',
                'media_service': False,
                'ptz_service': False,
                'imaging_service': False,
                'events_service': False
            }
            
            if hasattr(capabilities, 'Device') and capabilities.Device:
                if hasattr(capabilities.Device, 'XAddr'):
                    informacoes['capacidades']['onvif_service_url'] = capabilities.Device.XAddr
                    
            if hasattr(capabilities, 'Media') and capabilities.Media:
                informacoes['capacidades']['media_service'] = True
                
            if hasattr(capabilities, 'PTZ') and capabilities.PTZ:
                informacoes['capacidades']['ptz_service'] = True
                
            if hasattr(capabilities, 'Imaging') and capabilities.Imaging:
                informacoes['capacidades']['imaging_service'] = True
                
            if hasattr(capabilities, 'Events') and capabilities.Events:
                informacoes['capacidades']['events_service'] = True
                    
        except Exception as e:
            print(f"   ⚠️  Aviso ao obter capacidades: {e}")
            informacoes['aviso_capacidades'] = str(e)
        
        try:
            # Configurações de rede simplificadas
            print(f"   🌐 Obtendo configurações de rede...")
            network_interfaces = device_service.GetNetworkInterfaces()
            
            informacoes['rede'] = {
                'interface_ativa': False,
                'endereco_ip': 'N/A',
                'mascara_rede': 'N/A',
                'interface_nome': 'N/A'
            }
            
            if network_interfaces and len(network_interfaces) > 0:
                interface = network_interfaces[0]  # Primeira interface
                informacoes['rede']['interface_ativa'] = getattr(interface, 'Enabled', False)
                informacoes['rede']['interface_nome'] = getattr(interface, 'token', 'N/A')
                
                if hasattr(interface, 'IPv4') and interface.IPv4:
                    if hasattr(interface.IPv4, 'Config') and interface.IPv4.Config:
                        if hasattr(interface.IPv4.Config, 'Manual') and interface.IPv4.Config.Manual:
                            manual = interface.IPv4.Config.Manual[0]  # Primeira configuração manual
                            informacoes['rede']['endereco_ip'] = getattr(manual, 'Address', 'N/A')
                            informacoes['rede']['mascara_rede'] = getattr(manual, 'PrefixLength', 'N/A')
                    
        except Exception as e:
            print(f"   ⚠️  Aviso ao obter configurações de rede: {e}")
            informacoes['aviso_rede'] = str(e)
        
        try:
            # Horário do sistema simplificado
            print(f"   🕐 Obtendo horário do sistema...")
            system_time = device_service.GetSystemDateAndTime()
            
            informacoes['horario_sistema'] = {
                'timezone': 'N/A',
                'horario_local': 'N/A',
                'sincronizado': False
            }
            
            if system_time:
                if hasattr(system_time, 'TimeZone') and system_time.TimeZone:
                    informacoes['horario_sistema']['timezone'] = getattr(system_time.TimeZone, 'TZ', 'N/A')
                
                if hasattr(system_time, 'LocalDateTime') and system_time.LocalDateTime:
                    local_dt = system_time.LocalDateTime
                    if hasattr(local_dt, 'Date') and hasattr(local_dt, 'Time'):
                        date_part = local_dt.Date
                        time_part = local_dt.Time
                        informacoes['horario_sistema']['horario_local'] = f"{date_part.Year:04d}-{date_part.Month:02d}-{date_part.Day:02d} {time_part.Hour:02d}:{time_part.Minute:02d}:{time_part.Second:02d}"
                        informacoes['horario_sistema']['sincronizado'] = True
                
        except Exception as e:
            print(f"   ⚠️  Aviso ao obter horário: {e}")
            informacoes['aviso_horario'] = str(e)
        
        return informacoes

    def _salvar_informacoes(self, informacoes_cameras):
        """Salva as informações em arquivo JSON na pasta device_config"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_arquivo = f"camera_onvif_info_{timestamp}.json"
        caminho_arquivo = self.device_config_dir / nome_arquivo
        
        try:
            with open(caminho_arquivo, 'w', encoding='utf-8') as f:
                json.dump(informacoes_cameras, f, indent=2, ensure_ascii=False)
            
            print(f"\n💾 Informações salvas em: device_config/{nome_arquivo}")
            return str(caminho_arquivo)
            
        except Exception as e:
            print(f"❌ Erro ao salvar arquivo: {e}")
            return None

    def _exibir_resumo_final(self, informacoes_cameras, arquivo_salvo):
        """Exibe resumo final das informações obtidas"""
        print("📊 === RESUMO FINAL ===")
        print(f"Total de câmeras processadas: {len(informacoes_cameras)}")
        
        for camera_key, info in informacoes_cameras.items():
            status = "✅ CONECTADA" if info['conexao']['status'] == 'conectado' else "❌ FALHA"
            device_id = info['dispositivo'].get('serial_number', 'N/A')
            device_uuid = info['dispositivo'].get('device_uuid', 'N/A')
            modelo = info['dispositivo'].get('modelo', 'N/A')
            ip = info['configuracao'].get('ip', 'N/A')
            
            print(f"{camera_key.upper()}: {status}")
            print(f"   IP: {ip}")
            print(f"   Device ID/Serial: {device_id}")
            print(f"   Device UUID: {device_uuid}")
            print(f"   Modelo: {modelo}")
            print()
        
        if arquivo_salvo:
            caminho_relativo = Path(arquivo_salvo).relative_to(Path.cwd())
            print(f"📁 Arquivo completo salvo em: {caminho_relativo}")


# Funções legacy para compatibilidade com execução direta
def gerar_uuid_dispositivo(serial_number, fabricante="Motorola", modelo="MTIDM022603"):
    """Função legacy - usa o gerenciador"""
    manager = ONVIFDeviceManager()
    return manager._gerar_uuid_dispositivo(serial_number, fabricante, modelo)

def carregar_configuracoes():
    """Função legacy - usa o gerenciador"""
    manager = ONVIFDeviceManager()
    return manager._carregar_configuracoes()

def extrair_credenciais_rtsp(rtsp_url):
    """Função legacy - usa o gerenciador"""
    manager = ONVIFDeviceManager()
    return manager._extrair_credenciais_rtsp(rtsp_url)

def conectar_camera_onvif(ip, porta, usuario, senha):
    """Função legacy - usa o gerenciador"""
    manager = ONVIFDeviceManager()
    return manager._conectar_camera_onvif(ip, porta, usuario, senha)

def obter_informacoes_dispositivo(camera, device_service, ip):
    """Função legacy - usa o gerenciador"""
    manager = ONVIFDeviceManager()
    return manager._obter_informacoes_dispositivo(camera, device_service, ip)

def salvar_informacoes(informacoes_cameras):
    """Função legacy - usa o gerenciador"""
    manager = ONVIFDeviceManager()
    return manager._salvar_informacoes(informacoes_cameras)

def main():
    """Função principal quando executado diretamente"""
    print("🎥 === SCANNER DE INFORMAÇÕES DE CÂMERAS ONVIF ===")
    print("   Específico para Motorola IP DOME 2MP MTIDM022603")
    print("   📋 Inclui geração de UUID baseado no Device ID/Serial")
    print("   📁 JSON salvo na pasta device_config/\n")
    
    manager = ONVIFDeviceManager()
    informacoes = manager.obter_informacoes_cameras()
    
    if informacoes:
        print("\n✅ Processo concluído com sucesso!")
    else:
        print("\n❌ Processo concluído com problemas!")

if __name__ == "__main__":
    main()