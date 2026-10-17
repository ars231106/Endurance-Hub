"""Transactional email delivery.

Three transports, picked automatically from whichever credentials are
present. HTTP providers come first because most cloud hosts (Render
included) block outbound SMTP ports entirely as an anti-spam measure -
port 465 there fails with "Network is unreachable" no matter how correct
the Gmail credentials are. HTTPS on 443 is never blocked.

Nothing here raises. Email is a SUPPORTING service: the account row is
committed before the message is sent, so a delivery failure must not turn
a successful registration into a 500. When every transport is unavailable
the content goes to the log, which keeps a deployment usable while mail is
being configured.
"""
import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

log = logging.getLogger(__name__)

OTP_SUBJECT = "Your EnduranceHub verification code"
RESET_SUBJECT = "Reset your EnduranceHub password"


def _otp_body(code: str) -> str:
    return (
        f"Your EnduranceHub verification code is: {code}\n\n"
        "It expires in 10 minutes. If you didn't request this, ignore this email."
    )


def _reset_body(link: str) -> str:
    return (
        "Someone asked to reset the password on your EnduranceHub account.\n\n"
        f"Reset it here: {link}\n\n"
        "The link works once and expires in 30 minutes.\n"
        "If this wasn't you, ignore this email - your password has not changed."
    )


def _log_fallback(to_email: str, content: str, reason: str):
    # Printed rather than logged so it survives any log-level configuration;
    # this is the escape hatch that keeps a half-configured deploy usable.
    print(f"\n{'=' * 60}\n  [{reason}] for {to_email}:\n  {content}\n{'=' * 60}\n", flush=True)


def _post_json(url: str, payload: dict, headers: dict) -> None:
    # urlopen will happily follow file:// and custom schemes, which turns a
    # tainted URL into local file disclosure. These URLs are constants in
    # this module, but the check is cheap and keeps that true if they ever
    # become configurable.
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS mail endpoint: {url!r}")

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - scheme checked above
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


def _send_resend(to_email: str, subject: str, body: str):
    _post_json(
        "https://api.resend.com/emails",
        {
            "from": os.getenv("MAIL_FROM", "EnduranceHub <onboarding@resend.dev>"),
            "to": [to_email],
            "subject": subject,
            "text": body,
        },
        {"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
    )


def _send_brevo(to_email: str, subject: str, body: str):
    _post_json(
        "https://api.brevo.com/v3/smtp/email",
        {
            "sender": {"email": os.getenv("MAIL_FROM", ""), "name": "EnduranceHub"},
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": body,
        },
        {"api-key": os.environ["BREVO_API_KEY"]},
    )


def _send_smtp(to_email: str, subject: str, body: str):
    host = os.environ["SMTP_HOST"]
    user = os.environ["SMTP_USER"]
    port = int(os.getenv("SMTP_PORT", "465"))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email
    msg.set_content(body)

    # 465 is implicit TLS; 587 upgrades with STARTTLS after connecting.
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15) as server:
            server.login(user, os.environ["SMTP_PASS"])
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, os.environ["SMTP_PASS"])
            server.send_message(msg)


def _pick_transport():
    if os.getenv("RESEND_API_KEY"):
        return "Resend", _send_resend
    if os.getenv("BREVO_API_KEY"):
        return "Brevo", _send_brevo
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"):
        return "SMTP", _send_smtp
    return None, None


def _deliver(to_email: str, subject: str, body: str, fallback: str) -> bool:
    """Best-effort send. Returns True only if a provider accepted it."""
    transport, send = _pick_transport()
    if send is None:
        _log_fallback(to_email, fallback, "NO MAIL TRANSPORT CONFIGURED")
        return False

    try:
        send(to_email, subject, body)
        log.info("email sent", extra={"to": to_email, "transport": transport, "subject": subject})
        return True
    except urllib.error.HTTPError as exc:
        # The provider's body usually says exactly why: unverified sender,
        # quota exhausted, bad key.
        detail = exc.read().decode(errors="replace")[:300]
        log.error("mail rejected", extra={"transport": transport, "status": exc.code, "detail": detail})
        _log_fallback(to_email, fallback, f"{transport} REJECTED")
        return False
    except Exception as exc:  # noqa: BLE001 - delivery must never 500 a request
        log.error("mail delivery failed", extra={"transport": transport, "error": str(exc)})
        _log_fallback(to_email, fallback, f"{transport} FAILED")
        return False


def send_otp_email(to_email: str, code: str) -> bool:
    return _deliver(to_email, OTP_SUBJECT, _otp_body(code), f"OTP: {code}")


def send_password_reset_email(to_email: str, link: str) -> bool:
    return _deliver(to_email, RESET_SUBJECT, _reset_body(link), f"Reset link: {link}")
