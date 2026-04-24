import os
import re
import json
import subprocess
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import httpx
import io

# ─── Config ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("covertidos")

TEMP_DIR    = Path(os.getenv("TEMP_DIR",   "/home/covertidos/temp"))
OUTPUT_DIR  = Path(os.getenv("OUTPUT_DIR", "/home/covertidos/output"))
ASSETS_DIR  = Path(os.getenv("ASSETS_DIR", "/home/covertidos/assets"))

for d in [TEMP_DIR, OUTPUT_DIR, ASSETS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# OAuth2 tokens — injetados via variável de ambiente como JSON string
# Formato: {"token":..., "refresh_token":..., "token_uri":..., "client_id":..., "client_secret":..., "scopes":[...]}
GOOGLE_TOKENS_JSON = os.getenv("GOOGLE_TOKENS_JSON", "")
CLIENT_ID          = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET      = os.getenv("GOOGLE_CLIENT_SECRET", "")

FONT_PATH = "/usr/share/fonts/truetype/playfair/PlayfairDisplay-Bold.ttf"
LOGO_FILENAME = "logo_COVERTIDOS_preto_dourado.png"

app = FastAPI(title="CoverTidos API", version="1.0.0")

# ─── Models ───────────────────────────────────────────────────────────────────
class ProcessarRequest(BaseModel):
    file_id: str
    file_name: str
    title: str
    pasta_processando: str
    pasta_saida: str
    pasta_assets: str

class PublicarYouTubeRequest(BaseModel):
    video_path: str
    thumbnail_path: str
    titulo: str
    descricao: str
    tags: list[str]
    categoria: int = 10
    privacy_status: str = "unlisted"
    horario_publicacao: str = "18:00"
    timezone: str = "America/Sao_Paulo"

# ─── Helpers ──────────────────────────────────────────────────────────────────
def get_drive_service():
    if not GOOGLE_TOKENS_JSON:
        raise HTTPException(500, "GOOGLE_TOKENS_JSON não configurado")
    creds_data = json.loads(GOOGLE_TOKENS_JSON)
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=creds_data.get("scopes", ["https://www.googleapis.com/auth/drive"]),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds)

def get_youtube_service():
    if not GOOGLE_TOKENS_JSON:
        raise HTTPException(500, "GOOGLE_TOKENS_JSON não configurado")
    creds_data = json.loads(GOOGLE_TOKENS_JSON)
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=creds_data.get("scopes", [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube",
        ]),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)

def download_from_drive(service, file_id: str, dest_path: Path) -> Path:
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path

def sanitize_filename(name: str) -> str:
    return re.sub(r'[^\w\s\-]', '', name).strip().replace(' ', '_')

def gerar_video_ffmpeg(audio_path: Path, title: str, output_path: Path, logo_path: Optional[Path] = None) -> Path:
    """
    Gera vídeo 1920x1080 com:
    - Fundo preto
    - Título em dourado centralizado (Playfair Display Bold)
    - Logo no canto inferior direito (se disponível)
    - Áudio original
    """
    safe_title = title.replace("'", "\\'").replace(":", " -")
    duration_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)
    ]
    result = subprocess.run(duration_cmd, capture_output=True, text=True)
    duration = float(result.stdout.strip() or "180")

    # Filtros de vídeo
    vf_parts = [
        "color=c=#0a0a0a:size=1920x1080:rate=1[bg]",
        f"[bg]drawtext=fontfile='{FONT_PATH}':text='{safe_title}':fontcolor=#C9973A:fontsize=96:x=(w-text_w)/2:y=(h-text_h)/2-60:line_spacing=20[titled]",
        "[titled]drawtext=fontfile='{FONT_PATH}':text='CoverTidos':fontcolor=#F5F0E8:fontsize=42:x=(w-text_w)/2:y=(h-text_h)/2+80[watermarked]".format(FONT_PATH=FONT_PATH),
    ]

    if logo_path and logo_path.exists():
        vf_parts = [
            "color=c=#0a0a0a:size=1920x1080:rate=1[bg]",
            f"[bg]drawtext=fontfile='{FONT_PATH}':text='{safe_title}':fontcolor=#C9973A:fontsize=96:x=(w-text_w)/2:y=(h-text_h)/2-80:line_spacing=20[titled]",
        ]
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=#0a0a0a:size=1920x1080:rate=1:duration={duration}",
            "-i", str(audio_path),
            "-i", str(logo_path),
            "-filter_complex",
            f"[0:v]drawtext=fontfile='{FONT_PATH}':text='{safe_title}':fontcolor=#C9973A:fontsize=96:x=(w-text_w)/2:y=(h-text_h)/2-80[titled];"
            f"[titled]drawtext=fontfile='{FONT_PATH}':text='CoverTidos':fontcolor=#F5F0E8:fontsize=42:x=(w-text_w)/2:y=(h-text_h)/2+60[base];"
            f"[2:v]scale=300:-1[logo];"
            f"[base][logo]overlay=W-w-40:H-h-40[out]",
            "-map", "[out]",
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=#0a0a0a:size=1920x1080:rate=1:duration={duration}",
            "-i", str(audio_path),
            "-filter_complex",
            f"[0:v]drawtext=fontfile='{FONT_PATH}':text='{safe_title}':fontcolor=#C9973A:fontsize=96:x=(w-text_w)/2:y=(h-text_h)/2-40[titled];"
            f"[titled]drawtext=fontfile='{FONT_PATH}':text='CoverTidos':fontcolor=#F5F0E8:fontsize=42:x=(w-text_w)/2:y=(h-text_h)/2+80[out]",
            "-map", "[out]",
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ]

    logger.info(f"FFmpeg cmd: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg erro: {result.stderr[-500:]}")
    return output_path

def gerar_thumbnail(title: str, output_path: Path, logo_path: Optional[Path] = None) -> Path:
    """Gera thumbnail 1280x720 com Pillow"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1280, 720), color=(10, 10, 10))
    draw = ImageDraw.Draw(img)

    # Gradiente dourado sutil no fundo
    for i in range(720):
        alpha = int(20 * (i / 720))
        draw.line([(0, i), (1280, i)], fill=(alpha + 10, int(alpha * 0.6) + 5, 0))

    # Tentar usar fonte Playfair
    try:
        font_title = ImageFont.truetype(FONT_PATH, 90)
        font_sub   = ImageFont.truetype(FONT_PATH, 38)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub   = font_title

    # Título centralizado
    title_upper = title.upper()
    bbox = draw.textbbox((0, 0), title_upper, font=font_title)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Sombra
    draw.text(((1280 - tw) / 2 + 3, 720 / 2 - th / 2 - 60 + 3), title_upper, font=font_title, fill=(0, 0, 0))
    # Texto dourado
    draw.text(((1280 - tw) / 2, 720 / 2 - th / 2 - 60), title_upper, font=font_title, fill=(201, 151, 58))

    # Subtítulo
    sub = "CoverTidos • Louvor & Adoração"
    bbox2 = draw.textbbox((0, 0), sub, font=font_sub)
    sw = bbox2[2] - bbox2[0]
    draw.text(((1280 - sw) / 2, 720 / 2 + th / 2 + 20), sub, font=font_sub, fill=(245, 240, 232))

    # Logo canto inferior direito
    if logo_path and logo_path.exists():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((180, 180))
            img.paste(logo, (1280 - logo.width - 30, 720 - logo.height - 20), logo)
        except Exception as e:
            logger.warning(f"Logo thumbnail erro: {e}")

    img.save(str(output_path), "JPEG", quality=95)
    return output_path

def upload_to_drive(service, file_path: Path, folder_id: str, mime_type: str) -> str:
    media = MediaFileUpload(str(file_path), mimetype=mime_type)
    file_meta = {"name": file_path.name, "parents": [folder_id]}
    uploaded = service.files().create(body=file_meta, media_body=media, fields="id").execute()
    return uploaded.get("id")

def calcular_proximo_agendamento(horario: str = "18:00", tz: str = "America/Sao_Paulo") -> str:
    """Retorna próximo horário de publicação em RFC3339"""
    from zoneinfo import ZoneInfo
    hora, minuto = map(int, horario.split(":"))
    tz_obj = ZoneInfo(tz)
    now = datetime.now(tz_obj)
    scheduled = now.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if scheduled <= now:
        scheduled += timedelta(days=1)
    return scheduled.isoformat()

# ─── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "covertidos-api"}

@app.post("/covertidos/processar")
def processar(req: ProcessarRequest):
    logger.info(f"Processando: {req.file_name}")
    safe_name = sanitize_filename(req.title)

    try:
        drive = get_drive_service()

        # 1. Download do áudio
        audio_ext  = Path(req.file_name).suffix.lower()
        audio_path = TEMP_DIR / f"{safe_name}{audio_ext}"
        download_from_drive(drive, req.file_id, audio_path)
        logger.info(f"Áudio baixado: {audio_path}")

        # 2. Download do logo (se existir na pasta assets do Drive)
        logo_path = ASSETS_DIR / LOGO_FILENAME
        if not logo_path.exists():
            try:
                results = drive.files().list(
                    q=f"name='{LOGO_FILENAME}' and '{req.pasta_assets}' in parents and trashed=false",
                    fields="files(id,name)"
                ).execute()
                files = results.get("files", [])
                if files:
                    download_from_drive(drive, files[0]["id"], logo_path)
                    logger.info("Logo baixado do Drive")
            except Exception as e:
                logger.warning(f"Logo não encontrado no Drive: {e}")

        # 3. Gerar thumbnail
        thumb_path = OUTPUT_DIR / f"{safe_name}_thumb.jpg"
        gerar_thumbnail(req.title, thumb_path, logo_path if logo_path.exists() else None)
        logger.info(f"Thumbnail gerada: {thumb_path}")

        # 4. Gerar vídeo
        video_path = OUTPUT_DIR / f"{safe_name}.mp4"
        gerar_video_ffmpeg(audio_path, req.title, video_path, logo_path if logo_path.exists() else None)
        logger.info(f"Vídeo gerado: {video_path}")

        # 5. Upload MP4 para 03_saida no Drive
        video_drive_id = upload_to_drive(drive, video_path, req.pasta_saida, "video/mp4")
        logger.info(f"MP4 enviado ao Drive: {video_drive_id}")

        # 6. Limpar temp
        audio_path.unlink(missing_ok=True)

        return {
            "status": "ok",
            "video_path": str(video_path),
            "thumbnail_path": str(thumb_path),
            "video_drive_id": video_drive_id,
            "title": req.title,
            "safe_name": safe_name,
        }

    except Exception as e:
        logger.error(f"Erro ao processar {req.file_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/covertidos/publicar-youtube")
def publicar_youtube(req: PublicarYouTubeRequest):
    logger.info(f"Publicando no YouTube: {req.titulo}")

    try:
        youtube = get_youtube_service()

        publish_at = calcular_proximo_agendamento(req.horario_publicacao, req.timezone)

        body = {
            "snippet": {
                "title": req.titulo,
                "description": req.descricao,
                "tags": req.tags,
                "categoryId": str(req.categoria),
                "defaultLanguage": "pt",
                "defaultAudioLanguage": "pt",
            },
            "status": {
                "privacyStatus": req.privacy_status,
                "publishAt": publish_at if req.privacy_status == "unlisted" else None,
                "selfDeclaredMadeForKids": False,
            }
        }

        # Remove publishAt se não for unlisted/private
        if req.privacy_status == "public":
            body["status"].pop("publishAt", None)

        media = MediaFileUpload(req.video_path, mimetype="video/mp4", resumable=True, chunksize=10 * 1024 * 1024)
        insert_request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            _, response = insert_request.next_chunk()

        video_id = response.get("id")
        logger.info(f"Vídeo publicado: {video_id}")

        # Upload thumbnail
        if video_id and Path(req.thumbnail_path).exists():
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(req.thumbnail_path, mimetype="image/jpeg")
            ).execute()
            logger.info(f"Thumbnail enviada para {video_id}")

        return {
            "status": "ok",
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "publish_at": publish_at,
            "privacy": req.privacy_status,
        }

    except Exception as e:
        logger.error(f"Erro YouTube: {e}")
        raise HTTPException(status_code=500, detail=str(e))
