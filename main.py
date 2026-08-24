import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Audio Converter API",
    version="1.0.0"
)


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "downloads"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}


# ============================================================
# REQUEST MODEL
# ============================================================

class ConvertRequest(BaseModel):

    url: str

    format: str = "mp3"

    quality: str = "192"


# ============================================================
# URL VALIDATION
# ============================================================

def is_valid_url(url: str) -> bool:

    try:

        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:

        return False


# ============================================================
# DOWNLOAD SOURCE AUDIO
# ============================================================

def download_source(
    url: str,
    output_path: Path
):

    response = requests.get(
        url,
        stream=True,
        timeout=60,
        headers={
            "User-Agent": "AudioConverter/1.0"
        }
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "content-type",
        ""
    ).lower()

    # We expect an actual media resource,
    # not an HTML webpage.

    if "text/html" in content_type:

        raise RuntimeError(
            "The URL returned a webpage instead of an audio file."
        )

    with open(
        output_path,
        "wb"
    ) as output:

        for chunk in response.iter_content(
            chunk_size=1024 * 256
        ):

            if chunk:

                output.write(chunk)


# ============================================================
# FFMPEG CONVERSION
# ============================================================

def convert_to_mp3(
    source_path: Path,
    output_path: Path,
    quality: str
):

    quality_map = {
        "128": "128k",
        "192": "192k",
        "256": "256k",
        "320": "320k"
    }

    bitrate = quality_map.get(
        str(quality),
        "192k"
    )

    command = [
        "ffmpeg",

        "-y",

        "-i",
        str(source_path),

        "-vn",

        "-codec:a",
        "libmp3lame",

        "-b:a",
        bitrate,

        str(output_path)
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr[-3000:]
        )


# ============================================================
# BACKGROUND CONVERSION
# ============================================================

def process_job(
    job_id: str,
    url: str,
    quality: str
):

    source_path = (
        TEMP_DIR /
        f"{job_id}.source"
    )

    output_path = (
        OUTPUT_DIR /
        f"{job_id}.mp3"
    )

    try:

        # ----------------------------------------------------
        # PROCESSING
        # ----------------------------------------------------

        jobs[job_id]["status"] = "processing"
        jobs[job_id]["progress"] = 5

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        download_source(
            url,
            source_path
        )

        jobs[job_id]["progress"] = 40

        # ----------------------------------------------------
        # CONVERT
        # ----------------------------------------------------

        convert_to_mp3(
            source_path,
            output_path,
            quality
        )

        jobs[job_id]["progress"] = 100

        # ----------------------------------------------------
        # COMPLETED
        # ----------------------------------------------------

        jobs[job_id]["status"] = "completed"

        jobs[job_id]["downloadUrl"] = (
            f"/download/{job_id}"
        )

        jobs[job_id]["filename"] = (
            "converted_audio.mp3"
        )

    except Exception as error:

        jobs[job_id]["status"] = "failed"

        jobs[job_id]["progress"] = 0

        jobs[job_id]["error"] = str(error)

    finally:

        # ----------------------------------------------------
        # REMOVE TEMP SOURCE
        # ----------------------------------------------------

        try:

            if source_path.exists():

                source_path.unlink()

        except Exception:

            pass


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "service": "Audio Converter API",
        "version": "1.0.0"
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
# START CONVERSION
# ============================================================

@app.post("/convert")
def convert(
    request: ConvertRequest
):

    url = request.url.strip()

    # --------------------------------------------------------
    # URL CHECK
    # --------------------------------------------------------

    if not url:

        raise HTTPException(
            status_code=400,
            detail="URL is required."
        )

    if not is_valid_url(url):

        raise HTTPException(
            status_code=400,
            detail="Invalid URL."
        )

    # --------------------------------------------------------
    # FORMAT CHECK
    # --------------------------------------------------------

    if request.format.lower() != "mp3":

        raise HTTPException(
            status_code=400,
            detail="Only MP3 format is supported."
        )

    # --------------------------------------------------------
    # QUALITY CHECK
    # --------------------------------------------------------

    allowed_quality = {
        "128",
        "192",
        "256",
        "320"
    }

    quality = str(request.quality)

    if quality not in allowed_quality:

        quality = "192"

    # --------------------------------------------------------
    # CREATE JOB
    # --------------------------------------------------------

    job_id = str(uuid.uuid4())

    jobs[job_id] = {

        "jobId": job_id,

        "status": "queued",

        "progress": 0,

        "format": "mp3",

        "quality": quality
    }

    # --------------------------------------------------------
    # START WORKER
    # --------------------------------------------------------

    worker = threading.Thread(

        target=process_job,

        args=(
            job_id,
            url,
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
# CHECK JOB STATUS
# ============================================================

@app.get("/status/{job_id}")
def get_status(
    job_id: str
):

    job = jobs.get(job_id)

    if job is None:

        return {

            "jobId": job_id,

            "status": "not_found"
        }

    response = {

        "jobId": job_id,

        "status": job["status"],

        "progress": job.get(
            "progress",
            0
        )
    }

    # --------------------------------------------------------
    # COMPLETED
    # --------------------------------------------------------

    if job["status"] == "completed":

        response["downloadUrl"] = (
            job["downloadUrl"]
        )

        response["filename"] = (
            job["filename"]
        )

    # --------------------------------------------------------
    # FAILED
    # --------------------------------------------------------

    if job["status"] == "failed":

        response["error"] = job.get(
            "error",
            "Conversion failed."
        )

    return response


# ============================================================
# DOWNLOAD CONVERTED MP3
# ============================================================

@app.get("/download/{job_id}")
def download(
    job_id: str
):

    job = jobs.get(job_id)

    if job is None:

        raise HTTPException(
            status_code=404,
            detail="Job not found."
        )

    if job["status"] != "completed":

        raise HTTPException(
            status_code=409,
            detail="Conversion is not completed."
        )

    file_path = (
        OUTPUT_DIR /
        f"{job_id}.mp3"
    )

    if not file_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Converted file not found."
        )

    return FileResponse(

        path=file_path,

        media_type="audio/mpeg",

        filename="converted_audio.mp3"
    )


# ============================================================
# DELETE JOB
# ============================================================

@app.delete("/job/{job_id}")
def delete_job(
    job_id: str
):

    job = jobs.pop(
        job_id,
        None
    )

    if job is None:

        return {

            "status": "not_found"
        }

    file_path = (
        OUTPUT_DIR /
        f"{job_id}.mp3"
    )

    try:

        if file_path.exists():

            file_path.unlink()

    except Exception:

        pass

    return {

        "status": "deleted",

        "jobId": job_id
    }    }


def process_conversion(job_id: str, request: ConvertRequest):
    output_template = str(
        DOWNLOAD_DIR / f"{job_id}.%(ext)s"
    )

    try:
        jobs[job_id]["status"] = "processing"

        command = [
            "yt-dlp",
            "--no-playlist",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            f"{request.quality}K",
            "-o",
            output_template,
            request.url
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = (
                result.stderr[-1000:]
                if result.stderr
                else "Conversion failed"
            )
            return

        output_file = DOWNLOAD_DIR / f"{job_id}.mp3"

        if not output_file.exists():
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "Output file was not created"
            return

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["file"] = str(output_file)
        jobs[job_id]["filename"] = f"{job_id}.mp3"

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = "Conversion timed out"

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


@app.post("/convert")
def convert(request: ConvertRequest):

    if not request.url.strip():
        raise HTTPException(
            status_code=400,
            detail="URL is required"
        )

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "status": "queued",
        "url": request.url,
        "format": request.format,
        "quality": request.quality
    }

    worker = threading.Thread(
        target=process_conversion,
        args=(job_id, request),
        daemon=True
    )

    worker.start()

    return {
        "jobId": job_id,
        "status": "queued"
    }


@app.get("/status/{job_id}")
def status(job_id: str):

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

    if job["status"] == "completed":
        response["downloadUrl"] = (
            f"/download/{job_id}"
        )

        response["filename"] = job.get(
            "filename",
            f"{job_id}.mp3"
        )

    if job["status"] == "failed":
        response["error"] = job.get(
            "error",
            "Conversion failed"
        )

    return response


@app.get("/download/{job_id}")
def download(job_id: str):

    job = jobs.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    if job.get("status") != "completed":
        raise HTTPException(
            status_code=400,
            detail="File is not ready"
        )

    file_path = job.get("file")

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail="Converted file no longer exists"
        )

    return FileResponse(
        file_path,
        media_type="audio/mpeg",
        filename=job.get(
            "filename",
            f"{job_id}.mp3"
        )
    )
