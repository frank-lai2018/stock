from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import CORS_ORIGINS
from .routers import market, patterns, portfolio, screen, stock

app = FastAPI(title="stockselect API", description="台股選股系統後端（讀 twstock）", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(patterns.router)
app.include_router(portfolio.router)
app.include_router(screen.router)
app.include_router(stock.router)


@app.get("/api/health")
def health():
    return {"ok": True}
