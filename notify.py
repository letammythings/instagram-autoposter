import os
import smtplib
from email.mime.text import MIMEText


def send_alert(subject: str, body: str) -> None:
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    recipient = os.getenv("EMAIL_RECIPIENT")
    if not all([sender, password, recipient]):
        print(f"  [notify] Email env vars not set — skipping: {subject}")
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    print(f"  [notify] Alert sent: {subject}")
