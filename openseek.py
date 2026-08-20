#!/usr/bin/env python3
"""
OpenSeek - API com Auto Session
VERSÃO DEFINITIVA - Remove caracteres invisíveis no início da resposta
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
import traceback
import unicodedata
from typing import Optional, Dict, List, Any
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright

print("""
 ██████╗ ██████╗ ███████╗███╗   ██╗███████╗███████╗███████╗██╗  ██╗                   
██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔════╝██╔════╝██╔════╝██║ ██╔╝                         
██║   ██║██████╔╝█████╗  ██╔██╗ ██║███████╗█████╗  █████╗  █████╔╝                     
██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚════██║██╔══╝  ██╔══╝  ██╔═██╗                  
╚██████╔╝██║     ███████╗██║ ╚████║███████║███████╗███████╗██║  ██╗             
 ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝                 
A 100% free local API for DeepSeek AI models, developed by JuninVoid 
(with help from DeepSeek itself). Based on pedrofariasx/deepsproxy.
""")

# ============================================================================
# Configuração
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("openseek")

PORT = int(os.environ.get("PORT", "46191"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
AUTO_SESSION_FILE = "auto_session.json"

# ============================================================================
# Estado Global
# ============================================================================
class DeepSeekSession:
    """Gerencia uma sessão real do DeepSeek"""
    
    def __init__(self):
        self.chat_session_id = None
        self.parent_message_id = None
        self.messages = []
        self.created_at = time.time()
    
    def to_dict(self):
        return {
            "chat_session_id": self.chat_session_id,
            "parent_message_id": self.parent_message_id,
            "messages": self.messages,
            "created_at": self.created_at
        }
    
    def save(self):
        try:
            with open(AUTO_SESSION_FILE, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Error saving session: {e}")
    
    @classmethod
    def load(cls):
        try:
            if os.path.exists(AUTO_SESSION_FILE):
                with open(AUTO_SESSION_FILE, 'r') as f:
                    data = json.load(f)
                session = cls()
                session.chat_session_id = data.get("chat_session_id")
                session.parent_message_id = data.get("parent_message_id")
                session.messages = data.get("messages", [])
                session.created_at = data.get("created_at", time.time())
                return session
        except Exception as e:
            logger.error(f"Error loading session: {e}")
        return None

# Armazenamento
sessions: Dict[str, DeepSeekSession] = {}
auto_session: Optional[DeepSeekSession] = None
auto_session_id = "auto_session"

# Browser
_browser = None
_context = None
_page = None

# ============================================================================
# Browser Setup
# ============================================================================
async def init_browser():
    global _browser, _context, _page
    if _page:
        return
    
    logger.info("Starting browser...")
    pw = await async_playwright().start()
    _browser = pw
    _context = await pw.chromium.launch_persistent_context(
        "deepseek_profile",
        headless=HEADLESS,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    _page = await _context.new_page()
    logger.info("Browser ready")

async def close_browser():
    global _browser, _context, _page
    if _context:
        await _context.close()
    if _browser:
        await _browser.stop()
    _browser = None
    _context = None
    _page = None

# ============================================================================
# Headers (PoW)
# ============================================================================
async def get_headers(force_new: bool = False):
    global _page
    
    if not _page:
        raise RuntimeError("Browser not initialized")
    
    page = _page
    
    if "chat.deepseek.com" not in page.url or force_new:
        logger.info("Navigating to DeepSeek...")
        await page.goto("https://chat.deepseek.com/", wait_until="domcontentloaded")
        await asyncio.sleep(2)
    
    await page.wait_for_selector('textarea', timeout=15000)
    
    headers_result = {}
    event = asyncio.Event()
    
    async def route_handler(route, request):
        nonlocal headers_result
        headers = request.headers
        post_data = request.post_data
        
        chat_session = ""
        if post_data:
            try:
                payload = json.loads(post_data)
                chat_session = payload.get("chat_session_id", "")
            except:
                pass
        
        headers_result = {
            "authorization": headers.get("authorization", ""),
            "x-ds-pow-response": headers.get("x-ds-pow-response", ""),
            "x-hif-dliq": headers.get("x-hif-dliq", ""),
            "x-hif-leim": headers.get("x-hif-leim", ""),
            "cookie": headers.get("cookie", ""),
            "chat_session_id": chat_session
        }
        await route.abort("aborted")
        event.set()
    
    await page.route("**/api/v0/chat/completion", route_handler)
    await page.fill('textarea', 'a')
    await page.keyboard.press("Enter")
    
    try:
        await asyncio.wait_for(event.wait(), timeout=30)
    except asyncio.TimeoutError:
        await page.unroute("**/api/v0/chat/completion")
        raise RuntimeError("Timeout getting headers")
    
    await page.unroute("**/api/v0/chat/completion")
    
    try:
        await page.evaluate("""
            () => {
                const msgs = document.querySelectorAll('[data-message]');
                if (msgs.length > 0) msgs[msgs.length - 1].remove();
            }
        """)
    except:
        pass
    
    return headers_result

# ============================================================================
# Chat Core
# ============================================================================
async def send_to_deepseek(prompt: str, session: DeepSeekSession, thinking: bool = False, pro: bool = False):
    force_new = session.chat_session_id is None
    headers_info = await get_headers(force_new)
    
    if not session.chat_session_id and headers_info.get("chat_session_id"):
        session.chat_session_id = headers_info["chat_session_id"]
        logger.info(f"Got chat_session_id: {session.chat_session_id}")
    
    payload = {
        "chat_session_id": session.chat_session_id,
        "parent_message_id": session.parent_message_id,
        "prompt": prompt,
        "ref_file_ids": [],
        "thinking_enabled": thinking,
        "search_enabled": True,
        "preempt": False
    }
    
    if pro:
        payload["model_type"] = "expert"
    
    payload = {k: v for k, v in payload.items() if v is not None}
    
    logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://chat.deepseek.com/api/v0/chat/completion",
            headers={
                "accept": "*/*",
                "content-type": "application/json",
                "origin": "https://chat.deepseek.com",
                "authorization": headers_info.get("authorization", ""),
                "x-ds-pow-response": headers_info.get("x-ds-pow-response", ""),
                "x-hif-dliq": headers_info.get("x-hif-dliq", ""),
                "x-hif-leim": headers_info.get("x-hif-leim", ""),
            },
            json=payload
        )
        
        if response.status_code != 200:
            error_text = await response.aread()
            raise RuntimeError(f"API error {response.status_code}: {error_text.decode()}")
        
        return response.aiter_bytes()

# ============================================================================
# Parse Response - VERSÃO DEFINITIVA
# ============================================================================
def clean_response_text(text: str) -> str:
    """Limpa completamente o texto da resposta - Remove tudo que não é texto"""
    if not text:
        return ""
    
    # 🔥 REMOVE CARACTERES INVISÍVEIS DO INÍCIO
    # Lista de caracteres para remover do início
    chars_to_remove = [
        '\ufeff',  # BOM
        '\x00',    # Null
        '\x01',    # SOH
        '\x02',    # STX
        '\x03',    # ETX
        '\x04',    # EOT
        '\x05',    # ENQ
        '\x06',    # ACK
        '\x07',    # BEL
        '\x08',    # BS
        '\x09',    # TAB
        '\x0a',    # LF
        '\x0b',    # VT
        '\x0c',    # FF
        '\x0d',    # CR
        '\x0e',    # SO
        '\x0f',    # SI
        '\x10',    # DLE
        '\x11',    # DC1
        '\x12',    # DC2
        '\x13',    # DC3
        '\x14',    # DC4
        '\x15',    # NAK
        '\x16',    # SYN
        '\x17',    # ETB
        '\x18',    # CAN
        '\x19',    # EM
        '\x1a',    # SUB
        '\x1b',    # ESC
        '\x1c',    # FS
        '\x1d',    # GS
        '\x1e',    # RS
        '\x1f',    # US
        '\x7f',    # DEL
    ]
    
    # Remove caracteres do início
    for char in chars_to_remove:
        if text.startswith(char):
            text = text.lstrip(char)
    
    # Remove FINISHED residual
    text = text.replace('FINISHED', '')
    
    # Remove "data: " do início
    if text.startswith('data: '):
        text = text[6:]
    
    # 🔥 NORMALIZA UNICODE (NFKC)
    text = unicodedata.normalize('NFKC', text)
    
    # 🔥 REMOVE ESPAÇOS EM EXCESSO
    text = re.sub(r'\s+', ' ', text)
    
    # 🔥 CORRIGE PONTUAÇÃO
    # Remove espaços antes de pontuação
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)
    
    # Adiciona espaço após pontuação se não tiver
    text = re.sub(r'([.,!?;:])([^\s])', r'\1 \2', text)
    
    # 🔥 CORRIGE CARACTERES ACENTUADOS
    # Mapeamento de caracteres acentuados
    accent_map = {
        'á': 'á', 'à': 'á', 'ã': 'ã', 'â': 'â', 'ä': 'ä',
        'é': 'é', 'è': 'é', 'ê': 'ê', 'ë': 'ë',
        'í': 'í', 'ì': 'í', 'î': 'î', 'ï': 'ï',
        'ó': 'ó', 'ò': 'ó', 'õ': 'õ', 'ô': 'ô', 'ö': 'ö',
        'ú': 'ú', 'ù': 'ú', 'û': 'û', 'ü': 'ü',
        'ç': 'ç',
        'Á': 'Á', 'À': 'Á', 'Ã': 'Ã', 'Â': 'Â', 'Ä': 'Ä',
        'É': 'É', 'È': 'É', 'Ê': 'Ê', 'Ë': 'Ë',
        'Í': 'Í', 'Ì': 'Í', 'Î': 'Î', 'Ï': 'Ï',
        'Ó': 'Ó', 'Ò': 'Ó', 'Õ': 'Õ', 'Ô': 'Ô', 'Ö': 'Ö',
        'Ú': 'Ú', 'Ù': 'Ú', 'Û': 'Û', 'Ü': 'Ü',
        'Ç': 'Ç'
    }
    
    for key, value in accent_map.items():
        text = text.replace(key, value)
    
    # 🔥 CORRIGE NOMES PRÓPRIOS COM ESPAÇOS ENTRE LETRAS
    # Exemplo: "J UN IN VO ID" -> "JUNINVOID"
    words = text.split()
    if len(words) > 1:
        # Tenta juntar palavras que parecem parte de um nome
        fixed_words = []
        i = 0
        while i < len(words):
            word = words[i]
            # Se a palavra tem 1 letra e a próxima também, junta
            if len(word) == 1 and i + 1 < len(words):
                combined = word
                while i + 1 < len(words) and len(words[i + 1]) == 1:
                    combined += words[i + 1]
                    i += 1
                fixed_words.append(combined)
            else:
                fixed_words.append(word)
            i += 1
        text = ' '.join(fixed_words)
    
    # 🔥 REMOVE CARACTERES REPETIDOS NO INÍCIO
    # Exemplo: "á," -> "á," (mantém)
    # Mas remove caracteres repetidos sem sentido
    text = re.sub(r'^([,;:.])\1+', r'\1', text)
    
    # 🔥 CAPITALIZA PRIMEIRA LETRA (se for texto)
    if text and len(text) > 1:
        # Se começar com letra minúscula, capitaliza
        if text[0].islower():
            text = text[0].upper() + text[1:]
    
    return text.strip()

def clean_start_characters(text: str) -> str:
    """Remove caracteres indesejados APENAS do início do texto"""
    if not text:
        return ""
    
    # Caracteres para remover do início
    bad_starts = [
        'á,', 'à,', 'ã,', 'â,', 'ä,',
        'é,', 'è,', 'ê,', 'ë,',
        'í,', 'ì,', 'î,', 'ï,',
        'ó,', 'ò,', 'õ,', 'ô,', 'ö,',
        'ú,', 'ù,', 'û,', 'ü,',
        'ç,',
        ',', ';', ':', '.', '!', '?',
        'á', 'à', 'ã', 'â', 'ä',
        'é', 'è', 'ê', 'ë',
        'í', 'ì', 'î', 'ï',
        'ó', 'ò', 'õ', 'ô', 'ö',
        'ú', 'ù', 'û', 'ü',
        'ç'
    ]
    
    # Verifica se começa com algum caractere indesejado
    for start in bad_starts:
        if text.startswith(start):
            # Remove o caractere indesejado
            text = text[len(start):]
            break
    
    return text.strip()

async def parse_response(stream):
    """Parseia a resposta do DeepSeek - VERSÃO DEFINITIVA"""
    content = ""
    reasoning = ""
    buffer = ""
    parent_id = None
    finished_received = False
    chunk_count = 0
    
    # Buffer para acumular tudo antes de processar
    full_response = ""
    
    async for chunk in stream:
        chunk_count += 1
        buffer += chunk.decode("utf-8", errors="ignore")
        lines = buffer.split("\n")
        buffer = lines.pop() if lines else ""
        
        for line in lines:
            if not line.startswith("data: "):
                continue
            
            raw_data = line[6:].strip()
            
            # FILTRO 1: Remove [DONE] e FINISHED
            if raw_data in ["[DONE]", "FINISHED"]:
                logger.debug(f"Chunk {chunk_count}: Ignorando {raw_data}")
                continue
            
            # FILTRO 2: Remove chunks vazios
            if not raw_data:
                continue
            
            # FILTRO 3: Tenta parsear JSON
            try:
                chunk_data = json.loads(raw_data)
            except json.JSONDecodeError:
                # Pode ser um fragmento de texto puro
                if raw_data.strip():
                    full_response += raw_data
                continue
            
            # FILTRO 4: Ignora FINISHED em JSON
            if chunk_data.get("v") == "FINISHED":
                finished_received = True
                logger.debug(f"Chunk {chunk_count}: FINISHED ignorado")
                continue
            
            # Extrai parent_message_id
            if "response_message_id" in chunk_data:
                parent_id = chunk_data["response_message_id"]
                logger.debug(f"Chunk {chunk_count}: parent_id={parent_id}")
            
            # Extrai conteúdo
            v = chunk_data.get("v")
            
            if v is None:
                continue
            
            # Caso 1: v é dict com estrutura de fragments
            if isinstance(v, dict):
                # Tenta extrair fragments
                if "response" in v and "fragments" in v["response"]:
                    fragments = v["response"]["fragments"]
                    for frag in fragments:
                        if isinstance(frag, dict):
                            frag_content = frag.get("content", "")
                            frag_type = frag.get("type", "TEXT")
                            
                            if frag_content:
                                if frag_type == "THINK":
                                    reasoning += frag_content
                                else:
                                    full_response += frag_content
                # Tenta extrair content direto
                elif "content" in v:
                    full_response += v["content"]
            
            # Caso 2: v é string e NÃO é FINISHED
            elif isinstance(v, str) and v != "FINISHED":
                full_response += v
    
    # 🔥 PROCESSAMENTO FINAL DO TEXTO
    # Primeiro limpa caracteres do início
    content = clean_start_characters(full_response)
    
    # Depois limpa o texto completo
    content = clean_response_text(content)
    
    # 🔥 REMOVE CARACTERES INVISÍVEIS DO INÍCIO NOVAMENTE (garantia)
    content = content.lstrip('\ufeff').lstrip('\x00').strip()
    
    # Limpa reasoning
    reasoning = clean_response_text(reasoning).strip()
    
    logger.info(f"Processados {chunk_count} chunks")
    logger.info(f"Full response: {len(full_response)} caracteres")
    logger.info(f"Content: {len(content)} caracteres")
    logger.info(f"Reasoning: {len(reasoning)} caracteres")
    
    if content:
        logger.debug(f"Content preview: {content[:100]}...")
        # Mostra os primeiros caracteres em formato raw para debug
        logger.debug(f"Raw first 10 chars: {repr(content[:10])}")
    else:
        logger.warning("Conteúdo vazio!")
    
    # Fallback se conteúdo vazio
    if not content and not reasoning:
        content = "Desculpe, não consegui processar a resposta."
    
    return {
        "content": content,
        "reasoning": reasoning,
        "parent_id": parent_id,
        "finished": finished_received,
        "chunks": chunk_count
    }

# ============================================================================
# Process Message
# ============================================================================
async def process_message(message: str, session: DeepSeekSession, thinking: bool = False, pro: bool = False):
    """Processa uma mensagem e retorna a resposta"""
    
    # Envia para DeepSeek
    stream = await send_to_deepseek(message, session, thinking, pro)
    
    # Parseia resposta
    result = await parse_response(stream)
    
    # Atualiza parent_id
    if result["parent_id"]:
        session.parent_message_id = result["parent_id"]
        logger.info(f"Updated parent_id: {result['parent_id']}")
    
    # Salva no histórico
    session.messages.append({"role": "user", "content": message})
    session.messages.append({"role": "assistant", "content": result["content"]})
    
    return result

# ============================================================================
# FastAPI App
# ============================================================================
app = FastAPI()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global auto_session
    
    await init_browser()
    
    # Carrega sessão automática
    auto_session = DeepSeekSession.load()
    if auto_session:
        sessions[auto_session_id] = auto_session
        logger.info(f"Auto session loaded: {auto_session.chat_session_id}")
        logger.info(f"  messages: {len(auto_session.messages)}")
    else:
        auto_session = DeepSeekSession()
        sessions[auto_session_id] = auto_session
        logger.info("New auto session created")
    
    logger.info("Server ready")
    yield
    
    # Salva sessão automática antes de fechar
    if auto_session:
        auto_session.save()
        logger.info("Auto session saved")
    
    await close_browser()

app.router.lifespan_context = lifespan

# ============================================================================
# Endpoints - Health & Models
# ============================================================================
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": "deepseek-v4-flash"},
            {"id": "deepseek-v4-flash-thinking"},
            {"id": "deepseek-v4-pro"},
            {"id": "deepseek-v4-pro-thinking"}
        ]
    }

# ============================================================================
# Auto Session Endpoints
# ============================================================================
@app.get("/v1/auto/session")
async def get_auto_session():
    """Obtém informações da sessão automática"""
    session = sessions.get(auto_session_id)
    if not session:
        raise HTTPException(404, "Auto session not found")
    
    return {
        "session_id": auto_session_id,
        "chat_session_id": session.chat_session_id,
        "parent_message_id": session.parent_message_id,
        "messages_count": len(session.messages),
        "created_at": session.created_at,
        "last_messages": session.messages[-5:] if session.messages else []
    }

@app.post("/v1/auto/chat")
async def auto_chat(request: Request):
    """Chat usando a sessão automática"""
    global auto_session
    
    try:
        body = await request.json()
    except:
        raise HTTPException(400, "Invalid JSON")
    
    message = body.get("message", "")
    if not message:
        raise HTTPException(400, "Message is required")
    
    model = body.get("model", "deepseek-v4-flash")
    thinking = "thinking" in model
    pro = "pro" in model
    
    # Usa a sessão automática
    session = sessions.get(auto_session_id)
    if not session:
        session = DeepSeekSession()
        sessions[auto_session_id] = session
        auto_session = session
    
    try:
        # Processa a mensagem
        result = await process_message(message, session, thinking, pro)
        
        # Salva automaticamente
        session.save()
        
        return {
            "session_id": auto_session_id,
            "message": result["content"],
            "reasoning": result.get("reasoning"),
            "parent_id": session.parent_message_id,
            "chat_session_id": session.chat_session_id,
            "messages_count": len(session.messages)
        }
        
    except Exception as e:
        logger.error(f"Error: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(500, str(e))

@app.post("/v1/auto/reset")
async def reset_auto_session():
    """Reseta a sessão automática (mantém chat_session_id)"""
    global auto_session
    
    session = sessions.get(auto_session_id)
    if not session:
        raise HTTPException(404, "Auto session not found")
    
    # Limpa o histórico local
    session.messages = []
    session.save()
    
    return {
        "status": "reset",
        "session_id": auto_session_id,
        "chat_session_id": session.chat_session_id
    }

@app.post("/v1/auto/new")
async def new_auto_session():
    """Cria uma nova sessão automática"""
    global auto_session
    
    # Cria nova sessão
    auto_session = DeepSeekSession()
    sessions[auto_session_id] = auto_session
    auto_session.save()
    
    return {
        "status": "new_session_created",
        "session_id": auto_session_id
    }

@app.delete("/v1/auto/session")
async def delete_auto_session():
    """Deleta a sessão automática"""
    global auto_session
    
    if auto_session_id in sessions:
        del sessions[auto_session_id]
    
    if os.path.exists(AUTO_SESSION_FILE):
        os.remove(AUTO_SESSION_FILE)
    
    auto_session = None
    
    return {"status": "deleted"}

# ============================================================================
# Sessões Regulares (Compatibilidade)
# ============================================================================
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
        logger.info(f"Request: {json.dumps(body, indent=2)}")
    except Exception as e:
        logger.error(f"Invalid JSON: {e}")
        raise HTTPException(400, "Invalid JSON")
    
    model = body.get("model", "deepseek-v4-flash")
    messages = body.get("messages", [])
    session_id = body.get("session_id")
    force_new = body.get("force_new_session", False)
    
    thinking = "thinking" in model
    pro = "pro" in model
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    
    # Gerencia sessão
    if force_new or not session_id or session_id not in sessions:
        session_id = f"ses_{uuid.uuid4().hex[:8]}"
        sessions[session_id] = DeepSeekSession()
        logger.info(f"New session: {session_id}")
    else:
        logger.info(f"Using session: {session_id}")
    
    session = sessions[session_id]
    
    # Pega última mensagem do usuário
    user_message = None
    system_message = None
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role == "system":
            system_message = content
        elif role == "user":
            user_message = content
    
    prompt = user_message or ""
    if system_message:
        prompt = f"System: {system_message}\n\nUser: {user_message}"
    
    if not prompt:
        raise HTTPException(400, "No user message found")
    
    try:
        # Processa mensagem
        result = await process_message(prompt, session, thinking, pro)
        
        # Monta resposta
        message = {
            "role": "assistant",
            "content": result["content"]
        }
        if result.get("reasoning"):
            message["reasoning_content"] = result["reasoning"]
        
        response = {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "stop"
            }],
            "session_id": session_id,
            "deepseek_session": session.chat_session_id,
            "parent_id": session.parent_message_id
        }
        
        logger.info(f"Response sent: {completion_id}")
        return JSONResponse(response)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(500, str(e))

# ============================================================================
# Gerenciamento de Sessões
# ============================================================================
@app.get("/v1/sessions")
async def list_sessions():
    return {
        "sessions": [
            {
                "id": sid,
                "chat_session_id": s.chat_session_id,
                "parent_id": s.parent_message_id,
                "messages": len(s.messages),
                "created": s.created_at,
                "is_auto": sid == auto_session_id
            }
            for sid, s in sessions.items()
        ]
    }

@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    session = sessions[session_id]
    return {
        "id": session_id,
        "chat_session_id": session.chat_session_id,
        "parent_message_id": session.parent_message_id,
        "messages": session.messages,
        "created": session.created_at,
        "is_auto": session_id == auto_session_id
    }

@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    if session_id == auto_session_id:
        raise HTTPException(400, "Cannot delete auto session. Use /v1/auto/session DELETE")
    
    if session_id in sessions:
        del sessions[session_id]
        return {"status": "deleted"}
    raise HTTPException(404, "Session not found")

@app.post("/v1/sessions/{session_id}/clear")
async def clear_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    session = sessions[session_id]
    session.messages = []
    
    if session_id == auto_session_id:
        session.save()
    
    return {"status": "cleared"}

# ============================================================================
# Login
# ============================================================================
async def run_login():
    global HEADLESS
    HEADLESS = False
    await init_browser()
    print("\n🔓 Browser aberto! Faça login no DeepSeek.")
    print("Após fazer login, pressione Ctrl+C para continuar.\n")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    await close_browser()
    print("✅ Login salvo!")

# ============================================================================
# Tunnel
# ============================================================================
_tunnel_process = None

async def start_tunnel(port: int):
    global _tunnel_process
    try:
        proc = await asyncio.create_subprocess_exec(
            "cloudflared", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        if proc.returncode != 0:
            print("⚠️ cloudflared não encontrado")
            return None
    except:
        print("⚠️ cloudflared não encontrado")
        return None
    
    print(f"🚀 Iniciando tunnel na porta {port}...")
    _tunnel_process = await asyncio.create_subprocess_exec(
        "cloudflared", "tunnel", "--url", f"http://localhost:{port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    
    url_re = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    tunnel_url = None
    
    async def read_output():
        nonlocal tunnel_url
        while True:
            line = await _tunnel_process.stdout.readline()
            if not line:
                break
            text = line.decode().strip()
            match = url_re.search(text)
            if match:
                tunnel_url = match.group(0)
                print(f"✅ Tunnel ativo: {tunnel_url}")
    
    asyncio.create_task(read_output())
    
    for _ in range(20):
        if tunnel_url:
            return tunnel_url
        await asyncio.sleep(0.5)
    
    return None

# ============================================================================
# Main
# ============================================================================
async def main():
    import uvicorn
    
    tunnel_url = None
    if "--tunnel" in sys.argv:
        tunnel_url = await start_tunnel(PORT)
    
    print("""
    ╔══════════════════════════════════════════════╗
    ║  OpenSeek - API com Auto Session             ║
    ║  VERSÃO DEFINITIVA                          ║
    ║  Remove caracteres invisíveis do início     ║
    ╚══════════════════════════════════════════════╝
    """)
    print(f"🌐 Server: http://localhost:{PORT}")
    if tunnel_url:
        print(f"🔗 Pública: {tunnel_url}")
    print(f"📚 Sessões: /v1/sessions")
    print(f"🤖 Auto Session: /v1/auto/chat")
    print(f"📋 Auto Info: /v1/auto/session")
    
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        asyncio.run(run_login())
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n🛑 Desligando...")
            if _tunnel_process:
                _tunnel_process.terminate()