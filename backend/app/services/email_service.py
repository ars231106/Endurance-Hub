import logging
import os
import smtplib
from email.message import EmailMessage


def _log_code(to_email: str, code: str, reason: str):
    """Writes the code to the server log so it stays recoverable when mail
    isn't available - otherwise a delivery failure would lock every new
    account out permanently."""
    print(f"\n{'=' * 52}\n  [{reason}] OTP for {to_email}: {code}\n{'=' * 52}\n", flush=True)


def send_otp_email(to_email: str, code: str):
    """Sends the OTP via SMTP when credentials are configured, otherwise
    logs it. Never raises: registration must not fail because a mail
    server is unreachable, since the account row is already committed."""
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")

    if not (host and user and password):
        _log_code(to_email, code, "DEV MODE - no SMTP configured")
        return

    msg = EmailMessage()
    msg["Subject"] = "Your EnduranceHub verification code"
    msg["From"] = user
    msg["To"] = to_email
    msg.set_content(
        f"Your EnduranceHub verification code is: {code}\n\n"
        "It expires in 10 minutes. If you didn't request this, ignore this email."
    )

    port = int(os.getenv("SMTP_PORT", "465"))
    try:
        # Port 465 is implicit TLS (SMTP_SSL); 587 needs STARTTLS after
        # connecting. Some hosts block 465 outbound but allow 587.
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(user, password)
                server.send_message(msg)
        logging.info("Verification email sent to %s", to_email)
    except Exception as exc:  # noqa: BLE001 - delivery must never 500 the request
        logging.error("SMTP delivery failed for %s via %s:%s - %s", to_email, host, port, exc)
        _log_code(to_email, code, "SMTP FAILED - code logged instead")
