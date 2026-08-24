import os
import uuid
import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Audio Converter API")

DOWNLOAD_DIR = Path("/tmp/audio")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

jobs = {}


class ConvertRequest(BaseModel):
    url: str
    format: str = "mp3"
    quality: str = "192"


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Audio Converter API"
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }


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
