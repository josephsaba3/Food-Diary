"""Phone notifications through Web Push, and the high-stress alert that uses them.

Web Push is built into Android browsers and iPhone home-screen apps (iOS 16.4+): the phone hands the diary an
endpoint on its push service plus two keys, and the diary sends encrypted messages to that endpoint. Messages are
encrypted as RFC 8291 (aes128gcm) and signed with a VAPID key (RFC 8292), using only `cryptography`. The VAPID key
is derived from SESSION_SECRET, so it needs no setup and stays the same across restarts.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from datetime import timedelta, timezone
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select

from .health_view import high_stress_runs
from .models import PushSubscription, StressAlert, utcnow

log = logging.getLogger("food_diary.push")

ALERT_COOLDOWN = timedelta(hours=2)  # at most one stress alert this often, even if stress dips and climbs again
ALERT_FRESH_MINUTES = 120  # only announce stretches that ended this recently: Garmin data arrives late


def _aware(moment):
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment  # SQLite drops the zone


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf_expand(prk, info, length):
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _hkdf(salt, ikm, info, length):
    return _hkdf_expand(hmac.new(salt, ikm, hashlib.sha256).digest(), info, length)


def _raw_public(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(payload, p256dh, auth, salt=None, server_key=None):
    """Encrypt bytes for one subscription as a single aes128gcm record (RFC 8291 / RFC 8188)."""
    ua_public = unb64url(p256dh)
    auth_secret = unb64url(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(server_key)
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02: the last (and only) record
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + record


class Push:
    def __init__(self, settings, sessions, http=None):
        self.settings, self.sessions = settings, sessions
        self.http = http or requests.Session()
        seed = hmac.new(settings.session_secret.encode(), b"food-diary/vapid", hashlib.sha256).digest()
        order = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551  # P-256 group order
        self.vapid = ec.derive_private_key(int.from_bytes(seed, "big") % (order - 1) + 1, ec.SECP256R1())
        self.public_key = b64url(_raw_public(self.vapid))
        base = settings.base_url
        self.subject = base if base.startswith("https://") else "mailto:food-diary@example.invalid"

    # Subscriptions -------------------------------------------------------------

    def subscribe(self, endpoint, p256dh, auth, device=""):
        with self.sessions() as db:
            row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
            if row is None:
                row = PushSubscription(endpoint=endpoint, created_at=utcnow())
                db.add(row)
            row.p256dh, row.auth, row.device, row.last_error = p256dh, auth, device[:120], ""
            db.commit()
        return self.status()

    def unsubscribe(self, endpoint):
        with self.sessions() as db:
            db.execute(delete(PushSubscription).where(PushSubscription.endpoint == endpoint))
            db.commit()
        return self.status()

    def has_subscribers(self):
        with self.sessions() as db:
            return db.scalar(select(PushSubscription.id).limit(1)) is not None

    def status(self):
        with self.sessions() as db:
            subs = db.scalars(select(PushSubscription).order_by(PushSubscription.created_at)).all()
            alerts = db.scalars(select(StressAlert).order_by(StressAlert.sent_at.desc()).limit(5)).all()
            return {
                "public_key": self.public_key,
                "level": self.settings.stress_alert_level, "minutes": self.settings.stress_alert_minutes,
                "check_minutes": self.settings.stress_check_minutes,
                "devices": [{"endpoint": s.endpoint, "device": s.device, "created_at": s.created_at.isoformat(),
                             "last_error": s.last_error or None} for s in subs],
                "recent": [{"day": a.day.isoformat(), "start": f"{a.start_minute // 60:02d}:{a.start_minute % 60:02d}",
                            "minutes": a.minutes, "average": a.average, "peak": a.peak, "sent_at": a.sent_at.isoformat()}
                           for a in alerts],
            }

    # Sending -------------------------------------------------------------------

    def _vapid_header(self, endpoint):
        url = urlparse(endpoint)
        claims = {"aud": f"{url.scheme}://{url.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": self.subject}
        signing_input = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." + b64url(json.dumps(claims).encode())
        r, s = decode_dss_signature(self.vapid.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
        token = signing_input + "." + b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={token}, k={self.public_key}"

    def send(self, message, ttl=3 * 3600):
        """Send a notification ({title, body, url, tag}) to every subscribed device. Returns how many accepted it.
        Devices whose push service says the subscription is gone (404/410) are removed."""
        payload = json.dumps(message).encode()
        delivered = 0
        with self.sessions() as db:
            for sub in db.scalars(select(PushSubscription)).all():
                try:
                    response = self.http.post(sub.endpoint, data=encrypt(payload, sub.p256dh, sub.auth), timeout=15, headers={
                        "Authorization": self._vapid_header(sub.endpoint), "Content-Encoding": "aes128gcm",
                        "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": "high"})
                except (requests.RequestException, ValueError) as error:
                    sub.last_error = f"Couldn’t reach the push service: {type(error).__name__}"
                    continue
                if response.status_code in (404, 410):
                    db.delete(sub)
                elif response.status_code >= 400:
                    sub.last_error = f"Push service answered HTTP {response.status_code}"
                    log.warning("Push to %s failed: HTTP %s", urlparse(sub.endpoint).netloc, response.status_code)
                else:
                    sub.last_sent_at, sub.last_error = utcnow(), ""
                    delivered += 1
            db.commit()
        return delivered

    # High-stress alerts ----------------------------------------------------------

    def stress_stretch(self, slots, events, now_minute):
        """The latest stretch of high stress long enough to announce and recent enough to still matter, or None.
        Uses the 15-minute stress levels; without them (no Garmin account sign-in), Garmin's own stress periods."""
        level, needed = self.settings.stress_alert_level, self.settings.stress_alert_minutes
        runs = high_stress_runs(slots, level)
        if not any(isinstance(s.get("stress"), (int, float)) for s in slots or ()):
            runs = []
            for e in events or ():
                try:
                    start, end = (int(e[k][:2]) * 60 + int(e[k][3:5]) for k in ("start", "end"))
                except (KeyError, TypeError, ValueError):
                    continue
                if e.get("type") == "stress" and end > start:
                    runs.append({"start": start, "end": end, "minutes": end - start, "peak": None, "average": None})
        runs = [r for r in runs if r["minutes"] >= needed and r["end"] >= now_minute - ALERT_FRESH_MINUTES]
        return runs[-1] if runs else None

    def check_stress(self, day, slots, events, now_minute):
        """Send one alert for a new high-stress stretch today. Returns the alert sent, or None."""
        run = self.stress_stretch(slots, events, now_minute)
        if run is None:
            return None
        with self.sessions() as db:
            if db.scalar(select(StressAlert.id).where(StressAlert.day == day, StressAlert.start_minute == run["start"])):
                return None
            last = db.scalar(select(StressAlert.sent_at).order_by(StressAlert.sent_at.desc()).limit(1))
            if last is not None and _aware(last) > utcnow() - ALERT_COOLDOWN:
                return None
            db.add(StressAlert(day=day, start_minute=run["start"], minutes=run["minutes"], average=run["average"],
                               peak=run["peak"], sent_at=utcnow()))
            db.commit()
        clock = lambda m: f"{m // 60:02d}:{m % 60:02d}"
        hm = f"{run['minutes'] // 60}h {run['minutes'] % 60:02d}m" if run["minutes"] >= 60 else f"{run['minutes']} minutes"
        reading = f", averaging {run['average']} (peak {run['peak']})" if run["average"] is not None else ""
        message = {"title": f"High stress for {hm}",
                   "body": f"Your watch has read high stress since {clock(run['start'])}{reading}. "
                           "A good moment for whatever helps you wind down.",
                   "url": "/health#timeline", "tag": f"stress-{day.isoformat()}-{run['start']}"}
        self.send(message)
        return {**run, "message": message}

    def test(self):
        sent = self.send({"title": "Stress alerts are on",
                          "body": f"You’ll get a notification like this after {self.settings.stress_alert_minutes} minutes "
                                  f"of high stress ({self.settings.stress_alert_level}+).",
                          "url": "/settings#alerts", "tag": "test"}, ttl=600)
        return {"delivered": sent, **self.status()}

