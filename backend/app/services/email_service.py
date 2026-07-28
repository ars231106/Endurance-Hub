import os
import smtplib
from email.message import EmailMessage


def send_otp_email(to_email: str, code: str):
    """Sends the OTP via SMTP when credentials are configured;
    otherwise prints it to the server console (dev mode)."""
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")

    if not (host and user and password):
        # Dev mode: no email account needed - read the code off the terminal.
        print(f"\n{'=' * 46}\n  [DEV MODE] OTP for {to_email}: {code}\n{'=' * 46}\n")
        return

    msg = EmailMessage()
    msg["Subject"] = "Your EnduranceHub verification code"
    msg["From"] = user
    msg["To"] = to_email
    msg.set_content(
        f"Your EnduranceHub verification code is: {code}\n\n"
        "It expires in 10 minutes. If you didn't request this, ignore this email."
    )

    # SMTP_SSL handles Gmail (smtp.gmail.com, port 465) with an app password.
    with smtplib.SMTP_SSL(host, int(os.getenv("SMTP_PORT", "465"))) as server:
        server.login(user, password)
        server.send_message(msg)
