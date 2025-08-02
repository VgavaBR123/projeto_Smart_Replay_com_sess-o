# Sistema de Upload Offline - Documentação

## 📋 Visão Geral

O Sistema de Upload Offline foi implementado para garantir que vídeos sejam enviados automaticamente para o Supabase quando a conectividade com a internet for restaurada. O sistema monitora continuamente a conectividade e processa uma fila de uploads pendentes em background.

## 🏗️ Arquitetura

### Componentes Principais

1. **OfflineUploadManager** (`src/offline_upload_manager.py`)
   - Gerencia a fila de uploads offline
   - Monitora conectividade em background
   - Processa uploads automaticamente
   - Implementa retry logic com backoff exponencial

2. **Integração com CameraSystem** (`src/gravador_camera.py`)
   - Adiciona vídeos à fila quando offline
   - Métodos para monitorar status da fila
   - Funcionalidade para forçar processamento

3. **Banco de Dados SQLite** (`offline_data/upload_queue.db`)
   - Armazena fila de uploads persistente
   - Registra tentativas e erros
   - Log de conectividade

## 🔧 Configurações

Adicione as seguintes configurações no `config.env`:

```env
# Configurações de Upload Offline
OFFLINE_MAX_RETRY_ATTEMPTS=5          # Máximo de tentativas por arquivo
OFFLINE_RETRY_DELAY_BASE=60           # Delay base entre tentativas (segundos)
OFFLINE_CONNECTIVITY_CHECK_INTERVAL=30 # Intervalo de verificação de conectividade
OFFLINE_UPLOAD_BATCH_SIZE=3           # Número de uploads simultâneos
OFFLINE_MAX_QUEUE_SIZE=1000           # Tamanho máximo da fila
OFFLINE_EXPIRATION_HOURS=168          # Tempo para expirar uploads (7 dias)
OFFLINE_DELETE_AFTER_UPLOAD=true      # Deletar arquivos locais após upload
```

## 🚀 Como Funciona

### 1. Detecção de Estado Offline

Quando o sistema detecta que está offline (sem internet ou Supabase inacessível):
- Vídeos são salvos localmente
- Arquivos são automaticamente adicionados à fila de upload
- Mensagem é exibida: "Os vídeos serão enviados automaticamente quando a conectividade for restaurada"

### 2. Monitoramento Contínuo

O sistema executa em background:
- Verifica conectividade a cada 30 segundos (configurável)
- Quando detecta que está online, processa a fila
- Uploads são feitos em paralelo (3 simultâneos por padrão)

### 3. Sistema de Retry

- Máximo de 5 tentativas por arquivo
- Backoff exponencial entre tentativas
- Arquivos que excedem tentativas são marcados como falhados
- Limpeza automática de entradas antigas

## 📊 Estrutura do Banco de Dados

### Tabela `upload_queue`
```sql
CREATE TABLE upload_queue (
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
);
```

### Tabela `connectivity_log`
```sql
CREATE TABLE connectivity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL,
    latency REAL,
    error_details TEXT
);
```

## 🛠️ Uso Prático

### Integração Automática

O sistema funciona automaticamente quando você usa o `CameraSystem`:

```python
from src.gravador_camera import CameraSystem

# Inicializar sistema (upload offline é iniciado automaticamente)
camera_system = CameraSystem()

# Salvar vídeos (automaticamente adiciona à fila se offline)
camera_system.save_all_cameras()
```

### Monitoramento Manual

```python
# Verificar status da fila
status = camera_system.check_upload_queue_status()

# Forçar processamento da fila
result = camera_system.force_process_offline_queue()
```

### Uso Direto do OfflineUploadManager

```python
from src.offline_upload_manager import get_upload_manager

# Obter instância global
manager = get_upload_manager()

# Adicionar arquivo à fila
manager.add_to_queue(
    video_path="/path/to/video.mp4",
    camera_id="Camera_1",
    bucket_path="arena/quadra/video.mp4",
    session_id="session_123",
    arena="Arena_Principal",
    quadra="Quadra_1"
)

# Verificar status
status = manager.get_queue_status()
print(f"Pendentes: {status['pending']}")

# Forçar processamento
result = manager.force_process_queue()
```

## 🧪 Testes

Execute o script de teste para verificar o funcionamento:

```bash
python test_offline_upload.py
```

O script testa:
- Inicialização do sistema
- Verificação de conectividade
- Adição de arquivos à fila
- Processamento automático
- Integração com CameraSystem

## 📈 Monitoramento e Logs

### Logs do Sistema

O sistema usa o `system_logger` para registrar eventos:
- `log_info`: Informações gerais
- `log_success`: Uploads bem-sucedidos
- `log_warning`: Problemas não críticos
- `log_error`: Erros críticos
- `log_debug`: Informações detalhadas

### Status da Fila

```python
status = manager.get_queue_status()
# Retorna:
{
    'queue_size': 10,           # Total de itens na fila
    'pending': 5,               # Aguardando upload
    'completed': 4,             # Concluídos com sucesso
    'failed': 1,                # Falharam após todas as tentativas
    'recent_uploads_24h': 8,    # Uploads nas últimas 24h
    'is_monitoring': True,      # Se monitoramento está ativo
    'stats': {
        'total_processed': 15,
        'successful_uploads': 12,
        'failed_uploads': 3
    }
}
```

## 🔧 Manutenção

### Limpeza Automática

O sistema faz limpeza automática a cada 24 horas:
- Remove uploads concluídos há mais de 7 dias
- Remove uploads que excederam tentativas máximas
- Limpa logs de conectividade antigos

### Limpeza Manual

Para limpeza manual do banco:

```python
# Forçar limpeza
manager._cleanup_old_entries()

# Ou deletar banco inteiro (cuidado!)
import os
os.remove('offline_data/upload_queue.db')
```

## 🚨 Troubleshooting

### Problemas Comuns

1. **Sistema não detecta quando volta online**
   - Verifique se `OFFLINE_CONNECTIVITY_CHECK_INTERVAL` não está muito alto
   - Confirme se `network_checker.py` está funcionando

2. **Uploads não são processados**
   - Verifique logs para erros de conectividade
   - Confirme configurações do Supabase
   - Teste upload manual

3. **Fila cresce muito**
   - Ajuste `OFFLINE_MAX_QUEUE_SIZE`
   - Reduza `OFFLINE_EXPIRATION_HOURS`
   - Verifique se uploads estão falhando consistentemente

### Debug

```python
# Verificar conectividade manualmente
from src.network_checker import NetworkChecker
checker = NetworkChecker()
result = checker.check_full_connectivity()
print(result)

# Verificar logs de conectividade
import sqlite3
conn = sqlite3.connect('offline_data/upload_queue.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM connectivity_log ORDER BY timestamp DESC LIMIT 10")
for row in cursor.fetchall():
    print(row)
```

## 🔒 Segurança

- Arquivos são verificados antes do upload
- Paths são validados para evitar directory traversal
- Credenciais do Supabase são carregadas do ambiente
- Logs não expõem informações sensíveis

## 📝 Notas de Desenvolvimento

### Extensões Futuras

1. **Interface Web**: Dashboard para monitorar fila
2. **Notificações**: Alertas quando fila fica muito grande
3. **Compressão**: Compressão adicional para uploads offline
4. **Priorização**: Sistema mais sofisticado de prioridades
5. **Sincronização**: Sincronização entre múltiplos dispositivos

### Considerações de Performance

- SQLite é adequado para até ~1000 entradas simultâneas
- Para volumes maiores, considere PostgreSQL
- Uploads paralelos são limitados para não sobrecarregar rede
- Limpeza automática previne crescimento descontrolado

---

**✅ Sistema implementado e pronto para uso!**

O sistema de upload offline garante que nenhum vídeo seja perdido, mesmo com problemas de conectividade. Ele funciona de forma transparente e automática, sem necessidade de intervenção manual.