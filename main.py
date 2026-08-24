import os
import re
import uuid
import shutil
import threading
from pathlib import Path
from typing import Optional

import yt_dlp

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl


# ============================================================
# APP
# ============================================================

app = FastAPI(title="Audio Converter API")


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}

jobs_lock = threading.Lock()


# ============================================================
# REQUEST MODEL
# ============================================================

class ConvertRequest(BaseModel):
    url: HttpUrl
    format: str = "mp3"
    quality: str = "192"


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Audio Converter API"
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok"
    }


# ============================================================
# YOUTUBE URL VALIDATION
# ============================================================

def is_youtube_url(url: str) -> bool:

    url = url.lower().strip()

    allowed = (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be"
    )

    return any(domain in url for domain in allowed)


# ============================================================
# SAFE FILENAME
# ============================================================

def safe_filename(name: str) -> str:

    name = re.sub(
        r'[\\/*?:"<>|]',
        "",
        name
    )

    name = name.strip()

    if not name:
        name = "audio"

    return name[:150]


# ============================================================
# UPDATE JOB
# ============================================================

def update_job(
    job_id: str,
    **values
):

    with jobs_lock:

        if job_id in jobs:
            jobs[job_id].update(values)


# ============================================================
# CONVERSION WORKER
# ============================================================

def convert_audio(
    job_id: str,
    url: str,
    audio_format: str,
    quality: str
):

    output_template = str(
        DOWNLOAD_DIR / f"{job_id}.%(ext)s"
    )

    try:

        update_job(
            job_id,
            status="processing"
        )

        # ----------------------------------------------------
        # QUALITY
        # ----------------------------------------------------

        allowed_quality = {
            "128": "128",
            "192": "192",
            "256": "256",
            "320": "320"
        }

        bitrate = allowed_quality.get(
            str(quality),
            "192"
        )

        # ----------------------------------------------------
        # YT-DLP OPTIONS
        # ----------------------------------------------------

        options = {

            "format": "bestaudio/best",

            "outtmpl": output_template,

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            "postprocessors": [

                {
                    "key": "FFmpegExtractAudio",

                    "preferredcodec": audio_format,

                    "preferredquality": bitrate
                }
            ]
        }

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            title = info.get(
                "title",
                "audio"
            )

        # ----------------------------------------------------
        # OUTPUT FILE
        # ----------------------------------------------------

        generated_file = (
            DOWNLOAD_DIR /
            f"{job_id}.{audio_format}"
        )

        if not generated_file.exists():

            # Some FFmpeg configurations can
            # produce a different extension.

            candidates = list(
                DOWNLOAD_DIR.glob(
                    f"{job_id}.*"
                )
            )

            candidates = [
                file
                for file in candidates
                if file.is_file()
            ]

            if not candidates:
                raise FileNotFoundError(
                    "Converted audio file was not created."
                )

            generated_file = candidates[0]

        # ----------------------------------------------------
        # USER-FRIENDLY FILENAME
        # ----------------------------------------------------

        clean_title = safe_filename(title)

        final_file = (
            DOWNLOAD_DIR /
            f"{clean_title}.mp3"
        )

        # Avoid collisions

        if final_file.exists():

            final_file = (
                DOWNLOAD_DIR /
                f"{clean_title}_{job_id[:8]}.mp3"
            )

        shutil.move(
            str(generated_file),
            str(final_file)
        )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        update_job(

            job_id,

            status="completed",

            title=title,

            filename=final_file.name,

            downloadUrl=f"/download/{job_id}",

            filePath=str(final_file)
        )

    except Exception as error:

        update_job(

            job_id,

            status="failed",

            error=str(error)
        )


# ============================================================
# START CONVERSION
# ============================================================

@app.post("/convert")
def convert(request: ConvertRequest):

    url = str(request.url)

    # --------------------------------------------------------
    # VALIDATE URL
    # --------------------------------------------------------

    if not is_youtube_url(url):

        raise HTTPException(
            status_code=400,
            detail="Please provide a valid YouTube URL."
        )

    # --------------------------------------------------------
    # FORMAT
    # --------------------------------------------------------

    audio_format = (
        request.format.lower().strip()
    )

    if audio_format != "mp3":

        raise HTTPException(
            status_code=400,
            detail="Only MP3 format is supported."
        )

    # --------------------------------------------------------
    # QUALITY
    # --------------------------------------------------------

    quality = str(
        request.quality
    ).strip()

    if quality not in {
        "128",
        "192",
        "256",
        "320"
    }:

        quality = "192"

    # --------------------------------------------------------
    # JOB ID
    # --------------------------------------------------------

    job_id = str(
        uuid.uuid4()
    )

    # --------------------------------------------------------
    # CREATE JOB
    # --------------------------------------------------------

    with jobs_lock:

        jobs[job_id] = {

            "jobId": job_id,

            "status": "queued",

            "url": url,

            "format": audio_format,

            "quality": quality,

            "title": None,

            "filename": None,

            "downloadUrl": None,

            "filePath": None,

            "error": None
        }

    # --------------------------------------------------------
    # BACKGROUND THREAD
    # --------------------------------------------------------

    worker = threading.Thread(

        target=convert_audio,

        args=(
            job_id,
            url,
            audio_format,
            quality
        ),

        daemon=True
    )

    worker.start()

    # --------------------------------------------------------
    # RESPONSE
    # --------------------------------------------------------

    return {

        "jobId": job_id,

        "status": "queued"
    }


# ============================================================
# JOB STATUS
# ============================================================

@app.get("/status/{job_id}")
def status(job_id: str):

    with jobs_lock:

        job = jobs.get(job_id)

        if not job:

            return {
                "jobId": job_id,
                "status": "not_found"
            }

        response = {

            "jobId": job_id,

            "status": job["status"]
        }

        # ----------------------------------------------------
        # COMPLETED
        # ----------------------------------------------------

        if job["status"] == "completed":

            response.update({

                "title": job["title"],

                "filename": job["filename"],

                "downloadUrl":
                    job["downloadUrl"]
            })

        # ----------------------------------------------------
        # FAILED
        # ----------------------------------------------------

        if job["status"] == "failed":

            response["error"] = job["error"]

        return response


# ============================================================
# DOWNLOAD FILE
# ============================================================

@app.get("/download/{job_id}")
def download(job_id: str):

    with jobs_lock:

        job = jobs.get(job_id)

        if not job:

            raise HTTPException(
                status_code=404,
                detail="Job not found."
            )

        if job["status"] != "completed":

            raise HTTPException(
                status_code=409,
                detail="Audio is not ready yet."
            )

        file_path = job.get(
            "filePath"
        )

        filename = job.get(
            "filename"
        )

    # --------------------------------------------------------
    # VERIFY FILE
    # --------------------------------------------------------

    if not file_path:

        raise HTTPException(
            status_code=404,
            detail="Audio file not found."
        )

    file = Path(file_path)

    if not file.exists():

        raise HTTPException(
            status_code=404,
            detail="Audio file no longer exists."
        )

    # --------------------------------------------------------
    # RETURN FILE
    # --------------------------------------------------------

    return FileResponse(

        path=str(file),

        media_type="audio/mpeg",

        filename=filename or "audio.mp3"
    )


# ============================================================
# DELETE JOB / FILE
# ============================================================

@app.delete("/job/{job_id}")
def delete_job(job_id: str):

    with jobs_lock:

        job = jobs.get(job_id)

        if not job:

            raise HTTPException(
                status_code=404,
                detail="Job not found."
            )

        file_path = job.get(
            "filePath"
        )

        del jobs[job_id]

    # --------------------------------------------------------
    # DELETE FILE
    # --------------------------------------------------------

    if file_path:

        try:

            file = Path(file_path)

            if file.exists():
                file.unlink()

        except Exception:
            pass

    return {
        "status": "deleted",
        "jobId": job_id
    }
