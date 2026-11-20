"""Sign in to Garmin Connect on your own computer and print a token for the diary.

Use this when Garmin blocks the diary's server from signing in. It asks for your
Garmin email, password and (if enabled) the verification code, then prints the
garmin_tokens.json contents. Paste them into Settings -> Garmin -> "Paste a token".
Your password is only sent to Garmin. Treat the printed token like a password.

    pip install garminconnect
    python scripts/garmin_tokens.py
"""
import getpass

from garminconnect import Garmin


def main():
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    client = Garmin(email=email, password=password, prompt_mfa=lambda: input("Verification code: ").strip())
    client.login()
    print("\nSigned in as", client.full_name or client.display_name)
    print("Copy everything on the next line into the diary:\n")
    print(client.client.dumps())


if __name__ == "__main__":
    main()
