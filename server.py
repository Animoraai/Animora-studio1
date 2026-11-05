"""Animora v4: auth, quotas, Gemini -> Groq fallback. Demo deployment, not production hardened."""
import os, time, json, sqlite3, secrets, hashlib, hmac, asyncio, logging, re
from pathlib import Path
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent
# Render Free: writable ephemeral storage. Configure ANIMORA_DB_PATH for a persistent disk.
DB=Path(os.getenv('ANIMORA_DB_PATH', '/tmp/animora.db' if os.getenv('RENDER') else str(ROOT/'animora.db')))
DB.parent.mkdir(parents=True, exist_ok=True)
app=FastAPI(title='Animora Studio API')
logger=logging.getLogger('animora.ai')
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(levelname)s animora.ai %(message)s'))
    logger.addHandler(handler)
logger.propagate=False

def db():
    c=sqlite3.connect(DB,timeout=15)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,salt TEXT NOT NULL, password_hash TEXT NOT NULL,plan TEXT NOT NULL DEFAULT 'free')")
    c.execute('CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS usage(user_id INTEGER NOT NULL,day TEXT NOT NULL,kind TEXT NOT NULL,count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,day,kind))')
    c.execute('PRAGMA busy_timeout=15000')
    return c

class Credentials(BaseModel):
    email:str=Field(min_length=5,max_length=254)
    password:str=Field(min_length=8,max_length=200)
class ScriptRequest(BaseModel):
    title:str=Field(min_length=1,max_length=120)
    idea:str=Field(min_length=5,max_length=20000)
    genre:str=Field(default='Drama',max_length=100)
    language:str=Field(default='tr',pattern='^(tr|en)$')

def password_hash(p,salt): return hashlib.scrypt(p.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
def token_hash(t):return hashlib.sha256(t.encode()).hexdigest()
def today():return datetime.now(timezone.utc).date().isoformat()

def refund_usage(uid, day, kind):
    with db() as c:
        c.execute('UPDATE usage SET count=MAX(0,count-1) WHERE user_id=? AND day=? AND kind=?',(uid,day,kind))
def current_user(auth):
    if not auth or not auth.startswith('Bearer '):raise HTTPException(401,'Giriş gerekli')
    with db() as c:
        row=c.execute('SELECT u.id,u.email,u.plan FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?',(token_hash(auth[7:]),int(time.time()))).fetchone()
    if not row:raise HTTPException(401,'Oturum geçersiz veya süresi doldu')
    return row

def issue_token(c,uid):
    token=secrets.token_urlsafe(40)
    c.execute('INSERT INTO sessions VALUES(?,?,?)',(token_hash(token),uid,int(time.time())+86400*7))
    return token

@app.on_event('startup')
def initialize_database():
    with db() as c: c.execute('SELECT 1')

@app.post('/api/register')
def register(p:Credentials):
    email=p.email.strip().lower()
    if '@' not in email:raise HTTPException(400,'Geçersiz e-posta')
    salt=secrets.token_hex(16)
    try:
        with db() as c:
            cur=c.execute('INSERT INTO users(email,salt,password_hash) VALUES(?,?,?)',(email,salt,password_hash(p.password,salt)))
            token=issue_token(c,cur.lastrowid)
    except sqlite3.IntegrityError:raise HTTPException(409,'Bu e-posta kayıtlı')
    except sqlite3.Error:
        raise HTTPException(503,'Kayıt veritabanına erişilemiyor. Sunucu günlüklerini kontrol edin.')
    return {'token':token,'plan':'free'}

@app.post('/api/login')
def login(p:Credentials):
    with db() as c:
        row=c.execute('SELECT id,salt,password_hash,plan FROM users WHERE email=?',(p.email.strip().lower(),)).fetchone()
        if not row or not hmac.compare_digest(password_hash(p.password,row[1]) if row else '',row[2] if row else ''):
            raise HTTPException(401,'E-posta veya şifre yanlış')
        token=issue_token(c,row[0])
    return {'token':token,'plan':row[3]}

@app.post('/api/logout')
def logout(authorization:str|None=Header(default=None)):
    if authorization and authorization.startswith('Bearer '):
        with db() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(authorization[7:]),))
    return {'ok':True}

@app.get('/api/me')
def me(authorization:str|None=Header(default=None)):
    uid,email,plan=current_user(authorization)
    with db() as c:
        used=c.execute('SELECT count FROM usage WHERE user_id=? AND day=? AND kind=?',(uid,today(),'script')).fetchone()
    return {'email':email,'plan':plan,'script_remaining':max(0,(50 if plan=='premium' else 5)-(used[0] if used else 0))}

@app.get('/api/health')
def health():return {'status':'ok','gemini':bool(os.getenv('GEMINI_API_KEY')),'groq':bool(os.getenv('GROQ_API_KEY')),'cerebras':bool(os.getenv('CEREBRAS_API_KEY')),'openrouter':bool(os.getenv('OPENROUTER_API_KEY')),'mistral':bool(os.getenv('MISTRAL_API_KEY')),'huggingface_images':bool(os.getenv('HF_TOKEN')),'payments_enabled':False}

# Ordered text providers; a missing key simply skips that provider.
PROVIDERS = (
    ('gemini','GEMINI_API_KEY','GEMINI_MODEL','gemini-2.5-flash'),
    ('groq','GROQ_API_KEY','GROQ_MODEL','llama-3.3-70b-versatile'),
    ('cerebras','CEREBRAS_API_KEY','CEREBRAS_MODEL','llama3.1-8b'),
    ('openrouter','OPENROUTER_API_KEY','OPENROUTER_MODEL','openrouter/free'),
    ('mistral','MISTRAL_API_KEY','MISTRAL_MODEL','mistral-small-latest'),
)

@app.get('/api/providers')
def providers():
    return {'providers':[{'name':name,'configured':bool(os.getenv(key)),'model':os.getenv(model_var,default)} for name,key,model_var,default in PROVIDERS], 'image_provider':{'name':'huggingface','configured':bool(os.getenv('HF_TOKEN'))}, 'video_enabled':False}

def _safe_api_key(raw):
    # Do not silently change an invalid credential into a different credential.
    key = (raw or '').strip()
    if key.lower().startswith('bearer '):
        key = key[7:].strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in ("'", '"'):
        key = key[1:-1].strip()
    if not key or any(ord(c) < 33 or ord(c) == 127 for c in key):
        return None
    return key


def _gemini_text(data):
    candidates = data.get('candidates') or []
    if not candidates:
        raise ValueError('missing_candidates')
    parts = (candidates[0].get('content') or {}).get('parts') or []
    output = ''.join(part.get('text', '') for part in parts if isinstance(part, dict))
    if not output.strip():
        raise ValueError('empty_gemini_text')
    return output


async def generate(prompt, validator=None):
    # No credentials or raw provider response bodies are ever logged or returned.
    errors = []
    timeout = httpx.Timeout(60.0, connect=12.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        for name, key_var, model_var, default_model in PROVIDERS:
            raw = os.getenv(key_var, '')
            if not raw.strip():
                continue
            key = _safe_api_key(raw)
            if not key:
                errors.append(name + ': invalid_key_format')
                logger.warning('AI provider=%s invalid_key_format', name)
                continue
            configured_model = (os.getenv(model_var, default_model) or default_model).strip()
            models = [configured_model]
            # Retry alternate models only for model-not-found/retired responses.
            if name == 'groq' and configured_model != 'llama-3.1-8b-instant':
                models.append('llama-3.1-8b-instant')
            if name == 'gemini' and configured_model != 'gemini-2.5-flash-lite':
                models.append('gemini-2.5-flash-lite')
            for model in models:
                try:
                    if name == 'gemini':
                        url = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
                        r = await client.post(url, headers={'x-goog-api-key': key}, json={'contents': [{'parts': [{'text': prompt}]}]})
                        r.raise_for_status()
                        output = _gemini_text(r.json())
                    else:
                        urls = {'groq': 'https://api.groq.com/openai/v1/chat/completions', 'cerebras': 'https://api.cerebras.ai/v1/chat/completions', 'openrouter': 'https://openrouter.ai/api/v1/chat/completions', 'mistral': 'https://api.mistral.ai/v1/chat/completions'}
                        r = await client.post(urls[name], headers={'Authorization': 'Bearer ' + key}, json={'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.7})
                        r.raise_for_status()
                        output = r.json()['choices'][0]['message']['content']
                    if not isinstance(output, str) or not output.strip():
                        raise ValueError('empty_response')
                    if validator is not None:
                        try:
                            validator(output)
                        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
                            errors.append(name + ': invalid_storyboard_response')
                            logger.warning('AI provider=%s model=%s invalid_storyboard_response', name, model)
                            continue
                    return output, name
                except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                    status = getattr(getattr(exc, 'response', None), 'status_code', None)
                    code = str(status) if status is not None else type(exc).__name__
                    errors.append(name + ': ' + code)
                    logger.warning('AI provider=%s model=%s status=%s error_type=%s', name, model, status or 'network_or_parse', type(exc).__name__)
                    if not (name in ('groq','gemini') and status == 404 and model != models[-1]):
                        break
    logger.error('AI providers exhausted: %s', '; '.join(errors) if errors else 'no keys configured')
    raise HTTPException(503, 'AI sağlayıcıları başarısız: ' + ('; '.join(errors) if errors else 'API anahtarı tanımlı değil') + '. Anahtar değerlerini paylaşmayın.')

@app.post('/api/script')
async def script(p:ScriptRequest,authorization:str|None=Header(default=None)):
    uid,email,plan=current_user(authorization)
    limit=50 if plan=='premium' else 5
    # Reserve a usage slot before generation to prevent concurrent quota bypass.
    reserved_day=today()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        used=c.execute('SELECT count FROM usage WHERE user_id=? AND day=? AND kind=?',(uid,reserved_day,'script')).fetchone()
        if used and used[0]>=limit:raise HTTPException(429,'Günlük senaryo hakkınız doldu')
        c.execute('INSERT INTO usage(user_id,day,kind,count) VALUES(?,?,?,1) ON CONFLICT(user_id,day,kind) DO UPDATE SET count=count+1',(uid,reserved_day,'script'))
    prompt=f"You are a professional screenwriter. Write an original cinematic script treatment in {'Turkish' if p.language=='tr' else 'English'}. Include logline, three acts, consistent characters and five detailed scenes with visuals, camera directions and dialogue. Title: {p.title}\nGenre: {p.genre}\nIdea: {p.idea}"
    try: result,provider=await generate(prompt)
    except BaseException:
        refund_usage(uid,reserved_day,'script')
        raise
    return {'script':result,'provider':provider,'plan':plan}

def validate_duration(value):
    match=re.fullmatch(r'\s*(\d{1,3})\s*',value)
    if not match or not 1 <= int(match.group(1)) <= 120:
        raise ValueError('invalid_duration')
    return int(match.group(1))

class SceneAssistRequest(BaseModel):
    title:str=Field(min_length=1,max_length=120)
    idea:str=Field(default='',max_length=20000)
    synopsis:str=Field(default='',max_length=30000)
    characters:list[dict]=Field(default_factory=list,max_length=30)
    scene:dict=Field(default_factory=dict)
    kind:str=Field(pattern='^(visual|dialogue|camera|duration)$')
    language:str=Field(default='tr',pattern='^(tr|en)$')

@app.post('/api/scene-assist')
async def scene_assist(p:SceneAssistRequest,authorization:str|None=Header(default=None)):
    uid,email,plan=current_user(authorization)
    limit=50 if plan=='premium' else 5
    reserved_day=today()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        used=c.execute('SELECT count FROM usage WHERE user_id=? AND day=? AND kind=?',(uid,reserved_day,'storyboard')).fetchone()
        if used and used[0]>=limit:raise HTTPException(429,'Günlük storyboard AI hakkınız doldu')
        c.execute('INSERT INTO usage(user_id,day,kind,count) VALUES(?,?,?,1) ON CONFLICT(user_id,day,kind) DO UPDATE SET count=count+1',(uid,reserved_day,'storyboard'))
    task={'visual':'Write one detailed cinematic visual description for this scene, preserving each character lockedLook and story continuity. Do not generate an image or claim to have seen reference images.', 'dialogue':'Write natural, character-labeled dialogue appropriate to this scene and story.', 'camera':'Choose a cinematic camera shot, movement, and transition to the next shot. Return a single concise line.', 'duration':'Estimate the scene duration in seconds based on the scene action and dialogue. Reply with ONE INTEGER between 1 and 120 only.'}[p.kind]
    prompt=f"You are an expert film storyboard director. {task} Respond in {'Turkish' if p.language=='tr' else 'English'}. No markdown.\nTitle: {p.title}\nStory: {p.idea[:6000]}\nSynopsis: {p.synopsis[:6000]}\nCharacters: {json.dumps(p.characters,ensure_ascii=False)[:4000]}\nScene: {json.dumps(p.scene,ensure_ascii=False)[:4000]}"
    try: result,provider=await generate(prompt, validator=(lambda output: validate_duration(output)) if p.kind=='duration' else None)
    except BaseException:
        refund_usage(uid,reserved_day,'storyboard')
        raise
    if p.kind=='duration':
        import re
        value=validate_duration(result)
    else:value=result[:6000]
    return {'value':value,'provider':provider}

@app.get('/')
def home():return FileResponse(ROOT/'index.html')
@app.get('/hero-art.jpg')
def hero():return FileResponse(ROOT/'hero-art.jpg')


class StoryboardGenerateRequest(BaseModel):
    mode:str=Field(pattern='^(generate|continue)$')
    title:str=Field(min_length=1,max_length=120)
    idea:str=Field(default='',max_length=20000)
    synopsis:str=Field(default='',max_length=30000)
    characters:list[dict]=Field(default_factory=list,max_length=30)
    history:list[dict]=Field(default_factory=list,max_length=200)
    episode_index:int=Field(default=0,ge=0,le=199)
    language:str=Field(default='tr',pattern='^(tr|en)$')

def parse_storyboard(result):
    """Validate an AI storyboard before accepting a provider response."""
    clean=result.strip()
    clean=re.sub(r'^```(?:json)?\s*|\s*```$','',clean,flags=re.I)
    try:
        obj=json.loads(clean)
    except json.JSONDecodeError:
        match=re.search(r'\{.*\}',clean,re.S)
        if not match:raise ValueError('No JSON object in response')
        obj=json.loads(match.group(0))
    if not isinstance(obj,dict):raise ValueError('Not a JSON object')
    scenes=obj.get('scenes')
    if not isinstance(scenes,list) or not 1<=len(scenes)<=6:raise ValueError('Invalid scene count')
    output=[]
    for scene in scenes:
        if not isinstance(scene,dict):raise ValueError('Invalid scene type')
        if not isinstance(scene.get('visual'),str) or not scene['visual'].strip():raise ValueError('Missing visual')
        raw_duration=scene.get('duration',5)
        if isinstance(raw_duration,bool) or not isinstance(raw_duration,(int,str)):
            raise ValueError('Invalid duration type')
        try:duration=int(raw_duration)
        except (ValueError,TypeError):raise ValueError('Invalid duration')
        if not 1<=duration<=120:raise ValueError('Duration out of range')
        output.append({'visual':scene['visual'][:5000], 'dialogue':str(scene.get('dialogue',''))[:5000], 'camera':str(scene.get('camera',''))[:1000], 'duration':duration})
    return {'episode_title':str(obj.get('episode_title','Yeni Bölüm'))[:100], 'scenes':output}

@app.post('/api/storyboard-generate')
async def storyboard_generate(p:StoryboardGenerateRequest,authorization:str|None=Header(default=None)):
    uid,email,plan=current_user(authorization)
    limit=50 if plan=='premium' else 5
    reserved_day=today()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        used=c.execute('SELECT count FROM usage WHERE user_id=? AND day=? AND kind=?',(uid,reserved_day,'storyboard')).fetchone()
        if used and used[0]>=limit:raise HTTPException(429,'Günlük storyboard üretim hakkınız doldu')
        c.execute('INSERT INTO usage(user_id,day,kind,count) VALUES(?,?,?,1) ON CONFLICT(user_id,day,kind) DO UPDATE SET count=count+1',(uid,reserved_day,'storyboard'))
    # Existing episodes are supplied as context; the server does not yet persist projects.
    # Keep the latest episodes and synopsis within the provider's context budget.
    history=json.dumps(p.history[-12:],ensure_ascii=False)[:18000]
    characters=json.dumps(p.characters,ensure_ascii=False)[:6500]
    instruction=('Continue the plot AFTER the last existing episode. Do not retell existing events. Create a NEW episode with a meaningful next development.' if p.mode=='continue' else 'Generate the NEXT 3 to 5 storyboard scenes for the selected episode, building on existing scenes without repeating them.')
    prompt=f'''You are Animora, a cinematic series continuity editor. {instruction}
Language: {'Turkish' if p.language=='tr' else 'English'}.
STRICT CONTINUITY: preserve each established character's name, appearance, lockedLook (fixed face, hairstyle, clothes), personality, relationships and past events. Character references are local-only metadata and NOT visually available to you. Never claim to have analyzed a reference photo. Do not replace characters or contradict previous scenes. Keep the same setting and timeline unless the story explicitly changes them.
Project: {p.title}
Original premise: {p.idea[:4500]}
Story bible/synopsis: {p.synopsis[:6500]}
Established characters: {characters}
Previous episode and scene history: {history}
Selected episode index: {p.episode_index}
Return ONLY a valid JSON object with keys "episode_title" (string) and "scenes" (array of 3 to 5 objects). Each scene has keys "visual", "dialogue", "camera", "duration" (integer seconds 1-120). No markdown, no commentary. Scenes must be sequential, visually specific and include consistent character descriptions where applicable.'''
    try:
        result,provider=await generate(prompt, validator=parse_storyboard)
        parsed=parse_storyboard(result)
        return {**parsed,'provider':provider}
    except BaseException as exc:
        logger.error('Storyboard generation failed error_type=%s upstream_status=%s',type(exc).__name__,getattr(exc,'status_code','none'))
        refund_usage(uid,reserved_day,'storyboard')
        if isinstance(exc,HTTPException):raise
        if isinstance(exc,asyncio.CancelledError):raise
        raise HTTPException(502,'Storyboard AI yanıtı işlenemedi. Render Logs animora.ai kayıtlarını kontrol edin.') from exc


class ImageRequest(BaseModel):
    title:str=Field(min_length=1,max_length=120)
    visual:str=Field(min_length=5,max_length=4000)
    characters:list[dict]=Field(default_factory=list,max_length=30)
    aspect:str=Field(default='portrait',pattern='^(portrait|landscape)$')

@app.post('/api/image-generate')
async def image_generate(p:ImageRequest, authorization:str|None=Header(default=None)):
    uid,email,plan=current_user(authorization)
    key=os.getenv('HF_TOKEN')
    if not key:raise HTTPException(503,'Hugging Face token ayarlanmamış (HF_TOKEN).')
    # Strictly opt-in paid use: this integration does not guarantee free inference.
    # Admin must explicitly enable after checking account billing and limits.
    if os.getenv('ENABLE_IMAGE_API','false').lower()!='true':
        raise HTTPException(503,'Görsel API yöneticinin onayını bekliyor. Ücretsiz kota ve faturalandırmayı kontrol edin.')
    limit=20 if plan=='premium' else 2
    reserved_day=today()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        used=c.execute('SELECT count FROM usage WHERE user_id=? AND day=? AND kind=?',(uid,reserved_day,'image')).fetchone()
        if used and used[0]>=limit:raise HTTPException(429,'Günlük görsel üretim hakkınız doldu')
        c.execute('INSERT INTO usage(user_id,day,kind,count) VALUES(?,?,?,1) ON CONFLICT(user_id,day,kind) DO UPDATE SET count=count+1',(uid,reserved_day,'image'))
    character_text='; '.join(f"{str(c.get('name',''))[:80]}: {str(c.get('lockedLook') or c.get('description') or '')[:350]}" for c in p.characters[:12])
    prompt=f'Cinematic film storyboard, consistent characters, professional lighting. {p.visual}. Characters: {character_text}. No text, no watermark.'[:3500]
    try:
        from huggingface_hub import InferenceClient
        client=InferenceClient(api_key=key,provider=os.getenv('HF_IMAGE_PROVIDER','auto'))
        model=os.getenv('HF_IMAGE_MODEL','black-forest-labs/FLUX.1-schnell')
        picture=await asyncio.to_thread(client.text_to_image,prompt,model=model)
        import io
        buf=io.BytesIO();picture.save(buf,format='PNG')
        return Response(content=buf.getvalue(),media_type='image/png',headers={'Cache-Control':'no-store','X-Animora-Provider':'huggingface'})
    except BaseException:
        refund_usage(uid,reserved_day,'image')
        raise HTTPException(503,'Görsel üretimi başarısız. Model erişimini, kotayı ve servis durumunu kontrol edin.')
