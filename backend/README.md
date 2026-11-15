# Energy Checklist — Payment API (starter)

This folder contains the first backend scaffold for paid PDF reports. The checklist remains free. The backend stores the compressed report token, creates a ZarinPal payment order, verifies the callback server-side, and exposes a short QR URL at `/r/{public_id}`.

## Important status

This is an initial backend scaffold, **not yet connected to the live frontend**. Do not deploy it for real payments until the frontend flow, price, domain, HTTPS, database backups, and ZarinPal merchant settings are configured and tested in sandbox.

## Local setup (Windows CMD)

```cmd
cd backend
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`, then run:

```cmd
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check: `http://127.0.0.1:8000/health`

## Payment flow

1. The frontend prepares the existing compressed report token and calls `POST /api/payment/create`.
2. The API saves a pending order and sends the configured amount (Toman × 10 = Rial) to ZarinPal.
3. ZarinPal returns the user to `GET /api/payment/zarinpal/callback`.
4. The backend reads the amount from its own database and verifies the authority with ZarinPal.
5. Only after verification does the report become available at `/r/{public_id}`.
6. The QR embedded in the paid PDF must contain that short public URL. Scanning it redirects to the existing app with the full report token in its URL hash.

The report URL is a bearer link: anyone who has the QR/link can view that report. Do not place sensitive personal information in report data unless the report-sharing policy explicitly allows it.

## Production checklist

- Use a VPS with a fixed public IP and HTTPS.
- Set `PUBLIC_BASE_URL` to the backend HTTPS origin and `FRONTEND_APP_URL` to the final app URL on your domain.
- Use PostgreSQL and a strong database password.
- Set a real `PDF_PRICE_TOMAN` and a merchant ID obtained through the account holder's ZarinPal onboarding.
- Configure CORS with only the final frontend origin.
- Register/confirm the server's fixed IP in the merchant panel if required by ZarinPal.
- Run Uvicorn behind Nginx with systemd; do not use `--reload` in production.
- Keep `.env`, database backups, and logs outside the public web root.
- Test success, cancellation, duplicate callback, network failure, and forged callback cases in sandbox before enabling production.
