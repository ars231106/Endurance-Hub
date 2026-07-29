"""Verification email delivery.

Three transports, picked automatically from whichever credentials are
present. HTTP providers come first because most cloud hosts (Render
included) block outbound SMTP ports entirely as an anti-spam measure -
port 465 there fails with "Network is unreachable" no matter how correct
the Gmail credentials are. HTTPS on 443 is never blocked.

Nothing here raises: the account row is committed before the email is
sent, so a delivery failure must not turn a successful registration into
a 500. When every transport is unavailable the code goes to the log,
which keeps a deployment usable while mail is being configured.
"""
import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

SUBJECT = "Your EnduranceHub verification code"


def _body(code: str) -> str:
    return (
        f"Your EnduranceHub verification code is: {code}\n\n"
        "It expires in 10 minutes. If you didn't request this, ignore this email."
    )


def _log_code(to_email: str, code: str, reason: str):
    print(f"\n{'=' * 56}\n  [{reason}] OTP for {to_email}: {code}\n{'=' * 56}\n", flush=True)


def _post_json(url: str, payload: dict, headers: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


def _send_resend(to_email: str, code: str):
    _post_json(
        "https://api.resend.com/emails",
        {
            "from": os.getenv("MAIL_FROM", "EnduranceHub <onboarding@resend.dev>"),
            "to": [to_email],
            "subject": SUBJECT,
            "text": _body(code),
        },
        {"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
    )


def _send_brevo(to_email: str, code: str):
    sender = os.getenv("MAIL_FROM", "")
    _post_json(
        "https://api.brevo.com/v3/smtp/email",
        {
            "sender": {"email": sender, "name": "EnduranceHub"},
            "to": [{"email": to_email}],
            "subject": SUBJECT,
            "textContent": _body(code),
        },
        {"api-key": os.environ["BREVO_API_KEY"]},
    )


def _send_smtp(to_email: str, code: str):
    host = os.environ["SMTP_HOST"]
    user = os.environ["SMTP_USER"]
    port = int(os.getenv("SMTP_PORT", "465"))

    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = user
    msg["To"] = to_email
    msg.set_content(_body(code))

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


def send_otp_email(to_email: str, code: str):
    if os.getenv("RESEND_API_KEY"):
        transport, send = "Resend", _send_resend
    elif os.getenv("BREVO_API_KEY"):
        transport, send = "Brevo", _send_brevo
    elif os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"):
        transport, send = "SMTP", _send_smtp
    else:
        _log_code(to_email, code, "NO MAIL TRANSPORT CONFIGURED")
        return

    try:
        send(to_email, code)
        logging.info("Verification email sent to %s via %s", to_email, transport)
    except urllib.error.HTTPError as exc:
        # Provider rejected it - the body usually says exactly why
        # (unverified sender, quota, bad key).
        detail = exc.read().decode(errors="replace")[:300]
        logging.error("%s rejected mail for %s: HTTP %s %s", transport, to_email, exc.code, detail)
        _log_code(to_email, code, f"{transport} REJECTED - code logged instead")
    except Exception as exc:  # noqa: BLE001 - delivery must never 500 the request
        logging.error("%s delivery failed for %s: %s", transport, to_email, exc)
        _log_code(to_email, code, f"{transport} FAILED - code logged instead")
