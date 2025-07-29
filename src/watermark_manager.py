#!/usr/bin/env python3
"""
Sistema de Marca D'água Otimizado para Vídeos
Aplica marca d'água PNG com fundo azul marinho e círculo no canto inferior direito
Otimizado para CPU e RAM com cache inteligente
"""

import cv2
import numpy as np
import os
from pathlib import Path
import time


class WatermarkManager:
    def __init__(self, watermark_path=None):
        """
        Inicializa o gerenciador de marca d'água
        
        Args:
            watermark_path (str): Caminho para a imagem PNG da marca d'água
        """
        # Carregar configurações do ambiente
        # Cache para otimização
        self._watermark_cache = {}
        self._background_cache = {}
        
        # Configurações do config.env
        self.navy_blue = (139, 69, 19)  # BGR format - azul marinho
        self.watermark_path = watermark_path or os.getenv('WATERMARK_PATH', 
            r"c:\Users\Vinicius\PycharmProjects\Projeto_Camera_Seguranca_Otimizado\marca_dagua\Smart Byte - Horizontal.png")
        self.position = os.getenv('WATERMARK_POSITION', 'bottom_right')
        self.margin = int(os.getenv('WATERMARK_MARGIN', '25'))
        self.background_padding = int(os.getenv('WATERMARK_BACKGROUND_PADDING', '8'))
        self.circle_padding = int(os.getenv('WATERMARK_CIRCLE_PADDING', '5'))
        self.opacity = float(os.getenv('WATERMARK_OPACITY', '0.85'))
        
        # Novas configurações visuais
        self.shadow_enabled = os.getenv('WATERMARK_SHADOW_ENABLED', 'true').lower() == 'true'
        self.gradient_enabled = os.getenv('WATERMARK_GRADIENT_ENABLED', 'true').lower() == 'true'
        self.border_width = int(os.getenv('WATERMARK_BORDER_WIDTH', '2'))
        
        # Carregar marca d'água original
        self._load_watermark()
        
        print(f"✅ WatermarkManager inicializado")
        print(f"   📁 Marca d'água: {Path(self.watermark_path).name}")
        print(f"   🎨 Posição: {self.position}")
        print(f"   🔵 Estilo: Elegante com sombra e gradiente")
        print(f"   💫 Opacidade: {self.opacity}")
        print(f"   📏 Margem: {self.margin}px")
    
    def _load_watermark(self):
        """Carrega a marca d'água original"""
        if not os.path.exists(self.watermark_path):
            raise FileNotFoundError(f"Marca d'água não encontrada: {self.watermark_path}")
        
        # Carregar com canal alpha
        self.original_watermark = cv2.imread(self.watermark_path, cv2.IMREAD_UNCHANGED)
        
        if self.original_watermark is None:
            raise ValueError(f"Não foi possível carregar a marca d'água: {self.watermark_path}")
        
        # Se não tem canal alpha, criar um
        if self.original_watermark.shape[2] == 3:
            # Adicionar canal alpha (totalmente opaco)
            alpha = np.ones((self.original_watermark.shape[0], self.original_watermark.shape[1], 1), dtype=np.uint8) * 255
            self.original_watermark = np.concatenate([self.original_watermark, alpha], axis=2)
        
        print(f"📐 Marca d'água original: {self.original_watermark.shape[1]}x{self.original_watermark.shape[0]}")
    
    def _get_cached_watermark(self, frame_height, frame_width):
        """
        Obtém marca d'água em cache ou cria uma nova para o tamanho específico
        Com visual elegante: círculo menor, gradiente e sombra
        
        Args:
            frame_height (int): Altura do frame
            frame_width (int): Largura do frame
            
        Returns:
            tuple: (watermark_with_background, x_pos, y_pos)
        """
        cache_key = f"{frame_width}x{frame_height}"
        
        if cache_key in self._watermark_cache:
            return self._watermark_cache[cache_key]
        
        # Calcular tamanho da marca d'água (máximo 12% da largura do frame - menor que antes)
        max_width = int(frame_width * 0.12)
        original_height, original_width = self.original_watermark.shape[:2]
        
        # Manter proporção
        if original_width > max_width:
            scale = max_width / original_width
            new_width = max_width
            new_height = int(original_height * scale)
        else:
            new_width = original_width
            new_height = original_height
        
        # Redimensionar marca d'água
        watermark_resized = cv2.resize(self.original_watermark, (new_width, new_height), interpolation=cv2.INTER_AREA)
        
        # Calcular tamanho do círculo (mais compacto)
        circle_radius = max(new_width, new_height) // 2 + self.circle_padding
        shadow_offset = 3 if self.shadow_enabled else 0
        bg_size = circle_radius * 2 + self.background_padding * 2 + shadow_offset
        
        # Criar fundo transparente
        background = np.zeros((bg_size, bg_size, 4), dtype=np.uint8)
        
        # Posição do centro do círculo
        center_x = bg_size // 2 - shadow_offset // 2
        center_y = bg_size // 2 - shadow_offset // 2
        
        # 1. Desenhar sombra (se habilitada)
        if self.shadow_enabled:
            shadow_center = (center_x + shadow_offset, center_y + shadow_offset)
            cv2.circle(background, shadow_center, circle_radius, (0, 0, 0, int(80 * self.opacity)), -1)
            # Aplicar blur na sombra
            shadow_mask = background[:, :, 3] > 0
            if np.any(shadow_mask):
                background[:, :, 3] = cv2.GaussianBlur(background[:, :, 3], (5, 5), 0)
        
        # 2. Desenhar círculo principal com gradiente
        if self.gradient_enabled:
            # Criar gradiente radial
            y, x = np.ogrid[:bg_size, :bg_size]
            mask = (x - center_x) ** 2 + (y - center_y) ** 2 <= circle_radius ** 2
            
            # Calcular distância do centro para criar gradiente
            distances = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
            gradient_factor = np.clip(1.0 - (distances / circle_radius) * 0.3, 0.7, 1.0)
            
            # Aplicar gradiente no círculo
            for c in range(3):  # BGR
                color_value = self.navy_blue[c] * gradient_factor
                background[:, :, c][mask] = color_value[mask]
            
            # Canal alpha
            background[:, :, 3][mask] = int(255 * self.opacity)
        else:
            # Círculo sólido (fallback)
            cv2.circle(background, (center_x, center_y), circle_radius, (*self.navy_blue, int(255 * self.opacity)), -1)
        
        # 3. Adicionar borda elegante
        if self.border_width > 0:
            border_color = (180, 120, 60)  # Azul mais claro para a borda
            cv2.circle(background, (center_x, center_y), circle_radius, (*border_color, int(255 * self.opacity)), self.border_width)
        
        # 4. Calcular posição para centralizar a marca d'água no círculo
        watermark_x = center_x - new_width // 2
        watermark_y = center_y - new_height // 2
        
        # Garantir que a marca d'água não saia dos limites
        watermark_x = max(0, min(watermark_x, bg_size - new_width))
        watermark_y = max(0, min(watermark_y, bg_size - new_height))
        
        # 5. Aplicar marca d'água sobre o fundo com alpha blending melhorado
        wm_end_y = min(watermark_y + new_height, bg_size)
        wm_end_x = min(watermark_x + new_width, bg_size)
        wm_h = wm_end_y - watermark_y
        wm_w = wm_end_x - watermark_x
        
        if wm_h > 0 and wm_w > 0:
            watermark_crop = watermark_resized[:wm_h, :wm_w]
            
            for c in range(3):  # BGR channels
                alpha_watermark = watermark_crop[:, :, 3] / 255.0
                alpha_bg = background[watermark_y:wm_end_y, watermark_x:wm_end_x, 3] / 255.0
                
                # Alpha blending melhorado
                background[watermark_y:wm_end_y, watermark_x:wm_end_x, c] = (
                    watermark_crop[:, :, c] * alpha_watermark +
                    background[watermark_y:wm_end_y, watermark_x:wm_end_x, c] * (1 - alpha_watermark)
                ).astype(np.uint8)
            
            # Atualizar canal alpha
            background[watermark_y:wm_end_y, watermark_x:wm_end_x, 3] = np.maximum(
                watermark_crop[:, :, 3],
                background[watermark_y:wm_end_y, watermark_x:wm_end_x, 3]
            )
        
        # Calcular posição no frame (canto inferior direito)
        x_pos = frame_width - bg_size - self.margin
        y_pos = frame_height - bg_size - self.margin
        
        # Cache do resultado
        result = (background, x_pos, y_pos)
        self._watermark_cache[cache_key] = result
        
        print(f"🎨 Marca d'água elegante criada para {frame_width}x{frame_height}: {bg_size}x{bg_size} px")
        
        return result
    
    def apply_watermark(self, frame):
        """
        Aplica marca d'água no frame de forma otimizada
        
        Args:
            frame (numpy.ndarray): Frame do vídeo (BGR)
            
        Returns:
            numpy.ndarray: Frame com marca d'água aplicada
        """
        if frame is None:
            return frame
        
        frame_height, frame_width = frame.shape[:2]
        
        # Obter marca d'água em cache
        watermark_with_bg, x_pos, y_pos = self._get_cached_watermark(frame_height, frame_width)
        
        # Verificar se a marca d'água cabe no frame
        wm_height, wm_width = watermark_with_bg.shape[:2]
        
        if x_pos < 0 or y_pos < 0 or x_pos + wm_width > frame_width or y_pos + wm_height > frame_height:
            # Frame muito pequeno, pular marca d'água
            return frame
        
        # Criar cópia do frame para não modificar o original
        result_frame = frame.copy()
        
        # Extrair região do frame onde a marca d'água será aplicada
        frame_region = result_frame[y_pos:y_pos+wm_height, x_pos:x_pos+wm_width]
        
        # Aplicar marca d'água usando alpha blending otimizado
        alpha = watermark_with_bg[:, :, 3] / 255.0
        alpha_inv = 1.0 - alpha
        
        # Aplicar para cada canal BGR
        for c in range(3):
            frame_region[:, :, c] = (
                alpha * watermark_with_bg[:, :, c] +
                alpha_inv * frame_region[:, :, c]
            ).astype(np.uint8)
        
        # Aplicar região modificada de volta ao frame
        result_frame[y_pos:y_pos+wm_height, x_pos:x_pos+wm_width] = frame_region
        
        return result_frame
    
    def clear_cache(self):
        """Limpa o cache de marca d'água"""
        self._watermark_cache.clear()
        self._background_cache.clear()
        print("🧹 Cache de marca d'água limpo")
    
    def get_cache_info(self):
        """Retorna informações sobre o cache"""
        return {
            'watermark_cache_size': len(self._watermark_cache),
            'background_cache_size': len(self._background_cache),
            'cached_resolutions': list(self._watermark_cache.keys())
        }


def test_watermark():
    """Função de teste para verificar a marca d'água"""
    try:
        # Criar gerenciador
        wm = WatermarkManager()
        
        # Criar frame de teste
        test_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        test_frame[:] = (50, 50, 50)  # Fundo cinza
        
        # Aplicar marca d'água
        result = wm.apply_watermark(test_frame)
        
        # Salvar resultado de teste
        test_path = r"c:\Users\Vinicius\PycharmProjects\Projeto_Camera_Seguranca_Otimizado\test_watermark.jpg"
        cv2.imwrite(test_path, result)
        
        print(f"✅ Teste concluído - resultado salvo em: {test_path}")
        print(f"📊 Cache info: {wm.get_cache_info()}")
        
    except Exception as e:
        print(f"❌ Erro no teste: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_watermark()