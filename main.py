from fastapi import FastAPI
from pydantic import BaseModel
from uuid import uuid4

app = FastAPI(title="Audio Converter API")


class ConvertRequest(BaseModel):
    url: str
    format: str = "mp3"
    quality: str = "192"


jobs = {}


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Audio Converter API"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/convert")
def convert(request: ConvertRequest):

    job_id = str(uuid4())

    jobs[job_id] = {
        "status": "queued",
        "url": request.url,
        "format": request.format,
        "quality": request.quality
    }

    return {
        "jobId": job_id,
        "status": "queued"
    }


@app.get("/status/{job_id}")
def status(job_id: str):

    job = jobs.get(job_id)

    if not job:
        return {
            "status": "not_found"
        }

    return {
        "jobId": job_id,
        "status": job["status"]
    }
