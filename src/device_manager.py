import json
import uuid
import os
import hashlib
import platform
from pathlib import Path
from datetime import datetime
from system_logger import system_logger, log_debug, log_info, log_warning, log_error, log_success

class DeviceManager:
    def __init__(self, config_dir="device_config"):
        """
        Inicializa o gerenciador de dispositivo.
        
        Args:
            config_dir (str): Diretório onde será salvo o arquivo de configuração do dispositivo
        """
        self.config_dir = Path(config_dir)
        self.device_file = self.config_dir / "device_id.json"
        
        # Cria o diretório se não existir
        self.config_dir.mkdir(exist_ok=True)
        
    def _generate_hardware_id(self):
        """
        Gera um ID baseado em características do hardware do dispositivo.
        Isso garante que mesmo se o arquivo for perdido, o mesmo ID será gerado.
        """
        # Coleta informações únicas do sistema
        system_info = {
            'platform': platform.platform(),
            'processor': platform.processor(),
            'architecture': platform.architecture()[0],
            'machine': platform.machine(),
            'node': platform.node(),
        }
        
        # Cria uma string única baseada nas informações do sistema
        hardware_string = ''.join(str(value) for value in system_info.values())
        
        # Gera um hash SHA256 das informações do hardware
        hardware_hash = hashlib.sha256(hardware_string.encode()).hexdigest()
        
        # Converte o hash em um UUID determinístico
        device_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, hardware_hash))
        
        return device_uuid
    
    def _create_device_info(self):
        """
        Cria as informações completas do dispositivo.
        """
        device_id = self._generate_hardware_id()
        
        device_info = {
            "device_id": device_id,
            "created_at": datetime.now().isoformat(),
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "hostname": platform.node(),
            "immutable": True,
            "description": "Device ID único e imutável para sistema de câmeras de segurança"
        }
        
        return device_info
    
    def get_device_id(self):
        """
        Obtém o Device ID. Se não existir, cria um novo.
        Se existir, verifica a integridade e retorna o ID existente.
        Usa cache para evitar verificações repetitivas.
        
        Returns:
            str: Device ID único do dispositivo
        """
        try:
            # Verifica se já foi verificado no cache
            cached_device_id = system_logger.get_cached_verification('device_id')
            if cached_device_id:
                log_debug(f"Device ID do cache: {system_logger.get_device_id_short(cached_device_id)}")
                return cached_device_id
            
            # Verifica se o arquivo já existe
            if self.device_file.exists():
                # Carrega o arquivo existente
                with open(self.device_file, 'r', encoding='utf-8') as file:
                    device_data = json.load(file)
                
                # Verifica se o device_id existe e é válido
                if 'device_id' in device_data and device_data['device_id']:
                    device_id = device_data['device_id']
                    log_debug(f"Device ID encontrado: {device_id}")
                    
                    # Verifica a integridade comparando com o hardware atual
                    expected_id = self._generate_hardware_id()
                    if device_id == expected_id:
                        log_debug("Integridade do Device ID verificada com sucesso!")
                        # Armazena no cache
                        system_logger.cache_verification('device_id', device_id, 
                                                       f"Device ID verificado: {system_logger.get_device_id_short(device_id)}")
                        return device_id
                    else:
                        log_warning("Device ID no arquivo não corresponde ao hardware atual!")
                        log_warning("Isso pode indicar que o arquivo foi copiado de outro dispositivo.")
                        # Armazena no cache mesmo assim
                        system_logger.cache_verification('device_id', device_id)
                        return device_id  # Mantém o ID original mesmo assim
            
            # Se chegou aqui, precisa criar um novo Device ID
            log_info("Criando novo Device ID...")
            device_info = self._create_device_info()
            
            # Salva no arquivo
            with open(self.device_file, 'w', encoding='utf-8') as file:
                json.dump(device_info, file, indent=4, ensure_ascii=False)
            
            device_id = device_info['device_id']
            log_success(f"Novo Device ID criado: {system_logger.get_device_id_short(device_id)}")
            log_debug(f"Arquivo salvo em: {self.device_file.absolute()}")
            
            # Armazena no cache
            system_logger.cache_verification('device_id', device_id)
            
            return device_id
            
        except Exception as e:
            log_error(f"Erro ao gerenciar Device ID: {e}")
            # Em caso de erro, gera um ID temporário baseado no hardware
            fallback_id = self._generate_hardware_id()
            system_logger.cache_verification('device_id', fallback_id)
            return fallback_id
    
    def get_device_info(self):
        """
        Retorna todas as informações do dispositivo.
        
        Returns:
            dict: Informações completas do dispositivo
        """
        try:
            if self.device_file.exists():
                with open(self.device_file, 'r', encoding='utf-8') as file:
                    return json.load(file)
            else:
                # Se não existe, cria
                self.get_device_id()
                with open(self.device_file, 'r', encoding='utf-8') as file:
                    return json.load(file)
        except Exception as e:
            print(f"Erro ao obter informações do dispositivo: {e}")
            return {"error": str(e)}
    
    def verify_device_integrity(self):
        """
        Verifica se o Device ID armazenado corresponde ao hardware atual.
        
        Returns:
            bool: True se a integridade estiver ok, False caso contrário
        """
        try:
            if not self.device_file.exists():
                return False
            
            with open(self.device_file, 'r', encoding='utf-8') as file:
                device_data = json.load(file)
            
            stored_id = device_data.get('device_id')
            expected_id = self._generate_hardware_id()
            
            return stored_id == expected_id
            
        except Exception as e:
            print(f"Erro ao verificar integridade: {e}")
            return False


def main():
    """
    Função principal para testar o gerenciador de dispositivo.
    """
    print("=== GERENCIADOR DE DEVICE ID ===")
    print()
    
    # Cria uma instância do gerenciador
    device_manager = DeviceManager()
    
    # Obtém o Device ID
    device_id = device_manager.get_device_id()
    print(f"Device ID: {device_id}")
    print()
    
    # Verifica integridade
    integrity_ok = device_manager.verify_device_integrity()
    print(f"Integridade verificada: {'✓ OK' if integrity_ok else '✗ FALHA'}")
    print()
    
    # Mostra informações completas do dispositivo
    device_info = device_manager.get_device_info()
    print("=== INFORMAÇÕES DO DISPOSITIVO ===")
    for key, value in device_info.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()