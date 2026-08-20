# OpenSeek

**OpenSeek** é um proxy local que transforma o DeepSeek Web em uma API compatível com OpenAI. Ele automatiza o navegador para acessar o chat.deepseek.com e fornece uma interface RESTful para integrar o DeepSeek em seus aplicativos.

---

## 🚀 Principais Características

| Característica | Descrição |
|----------------|-----------|
| **API OpenAI-Compatible** | Use os mesmos endpoints do OpenAI |
| **Sessões Automáticas** | Mantém contexto entre conversas |
| **Persistência** | Sessões salvas em arquivo JSON |
| **Múltiplas Conversas** | Suporte a várias sessões simultâneas |
| **Cloudflare Tunnel** | Exponha sua API publicamente |
| **Tratamento de Texto** | Remove caracteres invisíveis e corrige acentos |
| **Modelos DeepSeek** | Flash, Pro, com e sem raciocínio |

---

## 📦 Instalação Rápida

```bash
#Baixar repositório
git clone https://github.com/juninvoidh/OpenSeek
cd OpenSeek
python3 -m venv venv
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt
playwright install chromium

# Fazer login
python3 session.py login

# Iniciar servidor
python3 session.py --tunnel
```

---

## 🔧 Endpoints Principais

### Auto Session (Recomendado)
```bash
# Chat contínuo
POST /v1/auto/chat
{
  "message": "Olá, como você está?",
  "model": "deepseek-v4-flash"
}

# Info da sessão
GET /v1/auto/session

# Resetar histórico
POST /v1/auto/reset
```

### Sessões Regulares (Compatível OpenAI)
```bash
# Chat com sessão
POST /v1/chat/completions
{
  "model": "deepseek-v4-flash",
  "messages": [{"role": "user", "content": "Olá"}],
  "session_id": "ses_xxx"  # Opcional
}

# Gerenciar sessões
GET /v1/sessions
GET /v1/sessions/{id}
DELETE /v1/sessions/{id}
POST /v1/sessions/{id}/clear
```

---

## 💬 Exemplo Rápido

```bash
# Auto Session
curl -X POST http://localhost:46191/v1/auto/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Meu nome é João"}'

# Resposta
{
  "message": "Olá João! Como posso ajudá-lo?",
  "messages_count": 2
}
```

---

## 🎯 Para que Serve?

- ✅ **Chatbots**: Integre DeepSeek em seus chatbots
- ✅ **Automação**: Crie agentes automatizados
- ✅ **Desenvolvimento**: Teste aplicações com IA
- ✅ **Estudos**: Experimente modelos DeepSeek
- ✅ **Produtos**: Adicione IA a seus produtos

---

## 🔥 Diferenciais

| OpenSeek | OpenAI API |
|----------|------------|
| **Grátis** | Pago |
| **Local** | Cloud |
| **Sem Limites** | Rate limits |
| **Privado** | Dados compartilhados |
| **DeepSeek** | GPT |

---

## 📊 Modelos Suportados

| Modelo | Descrição |
|--------|-----------|
| `deepseek-v4-flash` | Rápido e eficiente |
| `deepseek-v4-flash-thinking` | Com raciocínio explícito |
| `deepseek-v4-pro` | Mais poderoso |
| `deepseek-v4-pro-thinking` | Pro com raciocínio |

---

## 🛠️ Comandos Úteis

```bash
# Login
python3 session.py login

# Servidor normal
python3 session.py

# Com tunnel público
python3 session.py --tunnel

# Porta personalizada
PORT=46192 python3 session.py --tunnel
```

---

## 📝 Resumo

> **OpenSeek** = DeepSeek Web → API OpenAI-Compatible + Sessões Automáticas + Persistência + Grátis

Transforme o DeepSeek em uma API poderosa para seus projetos! 🚀
