import smtplib
from email.message import EmailMessage
from flask import current_app


def send_email(subject: str, body: str, recipients):
    """
    Send a plain-text email to the given list of recipients.
    Uses SMTP settings from app config.
    """
    if not recipients:
        return

    cfg = current_app.config

    host = cfg.get("SMTP_SERVER")
    port = cfg.get("SMTP_PORT")
    username = cfg.get("SMTP_USERNAME")
    password = cfg.get("SMTP_PASSWORD")
    from_addr = cfg.get("SMTP_FROM") or username

    if not host or not port or not from_addr:
        current_app.logger.warning("SMTP not fully configured; skipping email send.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port) as server:
            if cfg.get("SMTP_USE_TLS", True):
                server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    except Exception as exc:
        current_app.logger.error("Failed to send email: %r", exc)