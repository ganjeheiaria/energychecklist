import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

APP_URL = os.getenv("FRONTEND_APP_URL", "https://ganjeheiaria.github.io/energychecklist/app.html")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://api.example.com").rstrip("/")
MERCHANT_ID = os.getenv("ZARINPAL_MERCHANT_ID", "")
PRICE_TOMAN = int(os.getenv("PDF_PRICE_TOMAN", "0"))
ZARINPAL_API = os.getenv("ZARINPAL_API", "https://api.zarinpal.com")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./energychecklist.db")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class ReportOrder(Base):
    __tablename__ = "report_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    report_token: Mapped[str] = mapped_column(Text)
    amount_rial: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    authority: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

Base.metadata.create_all(engine)

app = FastAPI(title="Energy Checklist Payment API", version="0.1.0")
allowed_origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "https://ganjeheiaria.github.io").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

class PrepareReport(BaseModel):
    report_token: str = Field(min_length=20, max_length=20000)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/payment/create")
async def create_payment(data: PrepareReport):
    if not MERCHANT_ID or PRICE_TOMAN <= 0:
        raise HTTPException(status_code=503, detail="Payment is not configured yet.")
    public_id = secrets.token_urlsafe(32)
    amount_rial = PRICE_TOMAN * 10
    with SessionLocal() as db:
        order = ReportOrder(public_id=public_id, report_token=data.report_token, amount_rial=amount_rial)
        db.add(order)
        db.commit()
    payload = {
        "merchant_id": MERCHANT_ID,
        "amount": amount_rial,
        "callback_url": f"{PUBLIC_BASE_URL}/api/payment/zarinpal/callback",
        "description": "خرید گزارش PDF چک‌لیست انرژی ساختمان",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{ZARINPAL_API}/pg/v4/payment/request.json", json=payload)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        with SessionLocal() as db:
            order = db.scalar(select(ReportOrder).where(ReportOrder.public_id == public_id))
            if order:
                order.status = "gateway_error"
                db.commit()
        raise HTTPException(status_code=502, detail="Could not connect to payment gateway.")
    code = (result.get("data") or {}).get("code")
    authority = (result.get("data") or {}).get("authority")
    if code != 100 or not authority:
        raise HTTPException(status_code=502, detail={"message": "Payment request rejected", "gateway": result.get("errors") or result.get("data")})
    with SessionLocal() as db:
        order = db.scalar(select(ReportOrder).where(ReportOrder.public_id == public_id))
        order.authority = authority
        db.commit()
    return {"payment_url": f"https://www.zarinpal.com/pg/StartPay/{authority}", "order_id": public_id}

@app.get("/api/payment/zarinpal/callback")
async def zarinpal_callback(request: Request):
    authority = request.query_params.get("Authority", "")
    status = request.query_params.get("Status", "")
    with SessionLocal() as db:
        order = db.scalar(select(ReportOrder).where(ReportOrder.authority == authority))
        if not order:
            raise HTTPException(status_code=404, detail="Payment order not found.")
        if order.status == "paid":
            return RedirectResponse(f"{PUBLIC_BASE_URL}/r/{order.public_id}")
        if status.upper() != "OK":
            order.status = "cancelled"
            db.commit()
            return RedirectResponse(f"{APP_URL}?payment=failed")
        amount = order.amount_rial
    payload = {"merchant_id": MERCHANT_ID, "amount": amount, "authority": authority}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{ZARINPAL_API}/pg/v4/payment/verify.json", json=payload)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=502, detail="Could not verify payment. Please contact support.")
    data = result.get("data") or {}
    code = data.get("code")
    if code not in (100, 101):
        with SessionLocal() as db:
            order = db.scalar(select(ReportOrder).where(ReportOrder.authority == authority))
            if order and order.status != "paid":
                order.status = "failed"
                db.commit()
        return RedirectResponse(f"{APP_URL}?payment=failed")
    with SessionLocal() as db:
        order = db.scalar(select(ReportOrder).where(ReportOrder.authority == authority))
        if order and order.status != "paid":
            order.status = "paid"
            order.ref_id = str(data.get("ref_id", "")) or None
            order.paid_at = datetime.now(timezone.utc)
            db.commit()
        public_id = order.public_id
    return RedirectResponse(f"{PUBLIC_BASE_URL}/r/{public_id}")

@app.get("/r/{public_id}")
def open_public_report(public_id: str):
    with SessionLocal() as db:
        order = db.scalar(select(ReportOrder).where(ReportOrder.public_id == public_id))
        if not order or order.status != "paid":
            raise HTTPException(status_code=404, detail="Report not found.")
        token = order.report_token
    return RedirectResponse(f"{APP_URL}?paid_report={public_id}#r={token}")

@app.get("/api/reports/{public_id}/status")
def report_status(public_id: str):
    with SessionLocal() as db:
        order = db.scalar(select(ReportOrder).where(ReportOrder.public_id == public_id))
        if not order:
            raise HTTPException(status_code=404, detail="Report not found.")
        return {"status": order.status, "amount_rial": order.amount_rial, "ref_id": order.ref_id}
