#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerador de QR Code para Device ID
Gera QR code do Device ID e salva como imagem PNG e arquivo base64.
"""

import qrcode
import json
import base64
import io
from pathlib import Path
from datetime import datetime
from device_manager import DeviceManager

class QRCodeGenerator:
    def __init__(self, output_dir="qr_codes", device_manager=None):
        """
        Inicializa o gerador de QR code.
        
        Args:
            output_dir (str): Diretório onde serão salvos os arquivos do QR code
            device_manager (DeviceManager): Instância do DeviceManager (opcional)
        """
        self.output_dir = Path(output_dir)
        
        # Usa o DeviceManager fornecido ou cria um novo
        if device_manager:
            self.device_manager = device_manager
        else:
            self.device_manager = DeviceManager()
        
        # Cria o diretório se não existir
        self.output_dir.mkdir(exist_ok=True)
        
    def _create_qr_code(self, data):
        """
        Cria um QR code com os dados fornecidos.
        
        Args:
            data (str): Dados para incluir no QR code
            
        Returns:
            qrcode.QRCode: Objeto QR code configurado
        """
        qr = qrcode.QRCode(
            version=1,  # Controla o tamanho do QR code
            error_correction=qrcode.constants.ERROR_CORRECT_L,  # Correção de erro
            box_size=10,  # Tamanho de cada "caixa" do QR code
            border=4,  # Tamanho da borda
        )
        
        qr.add_data(data)
        qr.make(fit=True)
        
        return qr
    
    def generate_device_qr_code(self):
        """
        Gera QR code do Device ID e salva como PNG e base64.
        
        Returns:
            dict: Informações sobre os arquivos gerados
        """
        try:
            # Obtém o Device ID
            device_id = self.device_manager.get_device_id()
            device_info = self.device_manager.get_device_info()
            
            print(f"Gerando QR code para Device ID: {device_id}")
            print(f"⚡ QR code conterá APENAS o token: {device_id}")
            
            # QR code contém APENAS o Device ID (token puro)
            qr_string = device_id
            
            # Mantém os dados completos apenas para o arquivo de informações
            qr_data = {
                "device_id": device_id,
                "created_at": device_info.get("created_at"),
                "system": device_info.get("system"),
                "hostname": device_info.get("hostname"),
                "type": "security_camera_system"
            }
            
            # Cria o QR code
            qr = self._create_qr_code(qr_string)
            
            # Gera a imagem
            qr_image = qr.make_image(fill_color="black", back_color="white")
            
            # Define nomes dos arquivos
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            png_filename = f"device_qr_{device_id[:8]}_{timestamp}.png"
            base64_filename = f"device_qr_{device_id[:8]}_{timestamp}_base64.txt"
            info_filename = f"device_qr_{device_id[:8]}_{timestamp}_info.json"
            
            # Caminhos completos
            png_path = self.output_dir / png_filename
            base64_path = self.output_dir / base64_filename
            info_path = self.output_dir / info_filename
            
            # Salva a imagem PNG
            qr_image.save(png_path)
            print(f"✅ Imagem PNG salva: {png_path}")
            
            # Converte para base64
            img_buffer = io.BytesIO()
            qr_image.save(img_buffer, format='PNG')
            img_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
            
            # Salva o base64
            with open(base64_path, 'w', encoding='utf-8') as f:
                f.write(img_base64)
            print(f"✅ Arquivo base64 salvo: {base64_path}")
            print(f"🎯 Conteúdo do QR code: {qr_string} (apenas o token)")
            
            # Cria arquivo de informações
            qr_info = {
                "device_id": device_id,
                "qr_content": qr_string,  # QR code contém apenas o device_id
                "device_info": qr_data,   # Informações completas do dispositivo
                "files": {
                    "png_image": str(png_path),
                    "base64_file": str(base64_path),
                    "info_file": str(info_path)
                },
                "generated_at": datetime.now().isoformat(),
                "qr_string": qr_string,
                "qr_size": f"{qr_image.size[0]}x{qr_image.size[1]} pixels",
                "description": "QR code contém apenas o Device ID puro (token)"
            }
            
            # Salva as informações
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(qr_info, f, indent=4, ensure_ascii=False)
            print(f"✅ Arquivo de informações salvo: {info_path}")
            
            return qr_info
            
        except Exception as e:
            print(f"❌ Erro ao gerar QR code: {e}")
            return {"error": str(e)}
    
    def generate_simple_qr_code(self, custom_data=None):
        """
        Gera um QR code simples apenas com o Device ID.
        
        Args:
            custom_data (str): Dados customizados para o QR code (opcional)
            
        Returns:
            dict: Informações sobre os arquivos gerados
        """
        try:
            # Usa Device ID ou dados customizados
            if custom_data:
                qr_string = custom_data
                file_prefix = "custom_qr"
            else:
                device_id = self.device_manager.get_device_id()
                qr_string = device_id
                file_prefix = "simple_device_qr"
            
            print(f"Gerando QR code simples: {qr_string}")
            
            # Cria o QR code
            qr = self._create_qr_code(qr_string)
            qr_image = qr.make_image(fill_color="black", back_color="white")
            
            # Define nomes dos arquivos
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            png_filename = f"{file_prefix}_{timestamp}.png"
            base64_filename = f"{file_prefix}_{timestamp}_base64.txt"
            
            # Caminhos completos
            png_path = self.output_dir / png_filename
            base64_path = self.output_dir / base64_filename
            
            # Salva a imagem PNG
            qr_image.save(png_path)
            print(f"✅ Imagem PNG salva: {png_path}")
            
            # Converte para base64
            img_buffer = io.BytesIO()
            qr_image.save(img_buffer, format='PNG')
            img_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
            
            # Salva o base64
            with open(base64_path, 'w', encoding='utf-8') as f:
                f.write(img_base64)
            print(f"✅ Arquivo base64 salvo: {base64_path}")
            
            return {
                "qr_data": qr_string,
                "png_file": str(png_path),
                "base64_file": str(base64_path),
                "generated_at": datetime.now().isoformat(),
                "qr_size": f"{qr_image.size[0]}x{qr_image.size[1]} pixels"
            }
            
        except Exception as e:
            print(f"❌ Erro ao gerar QR code simples: {e}")
            return {"error": str(e)}
    
    def verificar_qr_existente(self):
        """
        Verifica se já existe um QR code válido para o device_id atual.
        
        Returns:
            dict: Informações sobre QR code existente
        """
        try:
            device_id = self.device_manager.get_device_id()
            device_prefix = f"device_qr_{device_id[:8]}_"
            
            # Procura por arquivos com o prefixo do device_id
            png_files = list(self.output_dir.glob(f"{device_prefix}*.png"))
            base64_files = list(self.output_dir.glob(f"{device_prefix}*_base64.txt"))
            
            if png_files and base64_files:
                # Encontrou arquivos válidos - usa o mais recente
                png_file = sorted(png_files)[-1]  # Mais recente
                base64_file = sorted(base64_files)[-1]  # Mais recente
                
                return {
                    'exists': True,
                    'valid': True,
                    'png_file': png_file,
                    'base64_file': base64_file,
                    'device_id': device_id
                }
            else:
                return {
                    'exists': False,
                    'valid': False,
                    'device_id': device_id
                }
                
        except Exception as e:
            print(f"❌ Erro ao verificar QR existente: {e}")
            return {
                'exists': False,
                'valid': False,
                'error': str(e)
            }
    
    def list_generated_qr_codes(self):
        """
        Lista todos os QR codes gerados.
        
        Returns:
            dict: Lista de arquivos QR code encontrados
        """
        qr_files = {
            "png_images": list(self.output_dir.glob("*.png")),
            "base64_files": list(self.output_dir.glob("*base64.txt")),
            "info_files": list(self.output_dir.glob("*info.json"))
        }
        
        return qr_files


def main():
    """
    Função principal para demonstrar o gerador de QR code.
    """
    print("🔳 GERADOR DE QR CODE PARA DEVICE ID")
    print("=" * 50)
    print()
    
    # Cria uma instância do gerador
    qr_generator = QRCodeGenerator()
    
    print("1️⃣ Gerando QR code completo com informações do dispositivo...")
    result1 = qr_generator.generate_device_qr_code()
    print()
    
    print("2️⃣ Gerando QR code simples apenas com Device ID...")
    result2 = qr_generator.generate_simple_qr_code()
    print()
    
    print("3️⃣ Listando arquivos gerados...")
    files = qr_generator.list_generated_qr_codes()
    
    print(f"📁 Imagens PNG: {len(files['png_images'])} arquivos")
    for png in files['png_images']:
        print(f"   - {png.name}")
    
    print(f"📄 Arquivos Base64: {len(files['base64_files'])} arquivos")
    for b64 in files['base64_files']:
        print(f"   - {b64.name}")
    
    print(f"📋 Arquivos Info: {len(files['info_files'])} arquivos")
    for info in files['info_files']:
        print(f"   - {info.name}")
    
    print()
    print("=" * 50)
    print("✅ QR CODES GERADOS COM SUCESSO!")
    print()
    print("📱 Use qualquer leitor de QR code para escanear as imagens")
    print("💾 Os arquivos base64 podem ser usados em aplicações web")
    print("📂 Todos os arquivos estão na pasta 'qr_codes'")


if __name__ == "__main__":
    main() 