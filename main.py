# d:\projects\test\dbhub-agent\main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import secrets
import uvicorn
from agent import ask_agent, init_agent, stream_agent
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-warm: discover tools + create agent at startup."""
    await init_agent()
    yield
    # Cleanup persistent HTTP client on shutdown
    from agent import _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()


security = HTTPBasic()
AUTH_USERNAME = os.getenv("AUTH_USERNAME")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Auth not configured")
        
    correct_username = secrets.compare_digest(credentials.username.encode("utf8"), AUTH_USERNAME.encode("utf8"))
    correct_password = secrets.compare_digest(credentials.password.encode("utf8"), AUTH_PASSWORD.encode("utf8"))
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

app = FastAPI(lifespan=lifespan, dependencies=[Depends(verify_auth)])

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def chat(request: ChatRequest):
    return StreamingResponse(stream_agent(request.message), media_type="application/x-ndjson")

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

# Serve static files (CSS, JS)
if not os.path.exists("static"):
    os.makedirs("static")

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
