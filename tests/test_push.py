import base64
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import date, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import health_view
from app.models import StressAlert, utcnow
from app.push import b64url, encrypt, unb64url
from tests.test_app import app, client  # noqa: F401  (fixtures)


class FakeResponse:
    def __init__(self, status):
        self.status_code = status


class FakePushService:
    def __init__(self, status=201):
        self.status, self.sent = status, []

    def post(self, url, data, headers, timeout):
        self.sent.append({"url": url, "data": data, "headers": headers})
        return FakeResponse(self.status)


def phone_keys():
    """A browser's subscription keys: its P-256 key pair and 16-byte auth secret."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    return key, b64url(public), b64url(auth)


def decrypt(body, key, auth):
    """What the phone does with a message (RFC 8291), written separately from app.push to check it."""
    hkdf = lambda salt, ikm, info, n: hmac.new(hmac.new(salt, ikm, hashlib.sha256).digest(), info + b"\x01", hashlib.sha256).digest()[:n]
    salt, record_size, id_len = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    server_public = body[21:21 + id_len]
    assert record_size == 4096 and id_len == 65
    ua_public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public))
    ikm = hkdf(unb64url(auth), shared, b"WebPush: info\x00" + ua_public + server_public, 32)
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + id_len:], None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def check_vapid(header, endpoint_origin):
    token, key = (part.split("=", 1)[1] for part in header.removeprefix("vapid ").split(", "))
    head, claims, signature = token.split(".")
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64url(key))
    raw = unb64url(signature)
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                  f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert json.loads(unb64url(head)) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(unb64url(claims))
    assert claims["aud"] == endpoint_origin and claims["sub"].startswith("mailto:")
    return key


def test_encryption_round_trips_for_the_phone():
    key, p256dh, auth = phone_keys()
    message = json.dumps({"title": "High stress for 30 minutes"}).encode()
    body = encrypt(message, p256dh, auth)
    assert decrypt(body, key, auth) == message
    assert encrypt(message, p256dh, auth) != body  # fresh salt and server key every time


def test_subscribe_test_alert_and_unsubscribe(app, client):
    service = FakePushService()
    app.state.push.http = service
    key, p256dh, auth = phone_keys()
    status = client.get("/api/push").json()
    assert len(unb64url(status["public_key"])) == 65 and status["level"] == 76 and status["minutes"] == 30
    sub = {"endpoint": "https://push.example.com/send/abc", "keys": {"p256dh": p256dh, "auth": auth}, "expirationTime": None}
    assert client.post("/api/push/subscribe", json={**sub, "device": "Android phone"}).status_code == 200
    assert client.post("/api/push/subscribe", json=sub).json()["devices"][0]["endpoint"] == sub["endpoint"]  # no duplicate
    assert client.post("/api/push/subscribe", json={**sub, "endpoint": "http://10.0.0.1/x"}).status_code == 422

    result = client.post("/api/push/test").json()
    assert result["delivered"] == 1 and len(service.sent) == 1
    sent = service.sent[0]
    assert sent["headers"]["Content-Encoding"] == "aes128gcm" and sent["headers"]["TTL"] == "600"
    assert check_vapid(sent["headers"]["Authorization"], "https://push.example.com") == status["public_key"]
    assert json.loads(decrypt(sent["data"], key, auth))["title"] == "Stress alerts are on"

    assert "STRESS_CHECK_MINUTES=0" in client.get("/settings").text  # tests run with the checks off
    app.state.push.settings = replace(app.state.push.settings, stress_check_minutes=30)
    page = client.get("/settings").text
    assert status["public_key"] in page and "every 30 minutes" in page and "On for 1 device." in page

    assert client.post("/api/push/unsubscribe", json={"endpoint": sub["endpoint"]}).json()["devices"] == []
    assert client.post("/api/push/test").json()["delivered"] == 0


def test_gone_subscriptions_are_removed(app, client):
    app.state.push.http = FakePushService(status=410)
    _, p256dh, auth = phone_keys()
    client.post("/api/push/subscribe", json={"endpoint": "https://push.example.com/old", "keys": {"p256dh": p256dh, "auth": auth}})
    assert client.post("/api/push/test").json()["delivered"] == 0
    assert client.get("/api/push").json()["devices"] == []


def slots_from(start, levels):
    h, m = map(int, start.split(":"))
    out = []
    for i, level in enumerate(levels):
        minute = h * 60 + m + i * 15
        out.append({"time": f"{minute // 60:02d}:{minute % 60:02d}", "stress": level, "stress_max": level + 5})
    return out


def test_stress_alert_is_sent_once_per_stretch(app, client):
    service = FakePushService()
    push = app.state.push
    push.http = service
    key, p256dh, auth = phone_keys()
    client.post("/api/push/subscribe", json={"endpoint": "https://push.example.com/a", "keys": {"p256dh": p256dh, "auth": auth}})
    day = date(2026, 11, 26)

    assert push.check_stress(day, slots_from("14:00", [40, 80, 30]), [], 15 * 60) is None  # only 15 minutes high
    assert push.check_stress(day, slots_from("09:00", [80, 85]), [], 15 * 60) is None  # ended too long ago
    slots = slots_from("14:00", [40, 80, 90, 82])
    alert = push.check_stress(day, slots, [], 15 * 60)
    assert alert["start"] == 14 * 60 + 15 and alert["minutes"] == 45 and alert["average"] == 84 and alert["peak"] == 95
    message = json.loads(decrypt(service.sent[-1]["data"], key, auth))
    assert message["title"] == "High stress for 45 minutes" and "since 14:15" in message["body"] and message["url"] == "/health#timeline"
    # The same stretch growing doesn't alert again, and a new stretch within two hours waits.
    assert push.check_stress(day, slots + slots_from("15:00", [88]), [], 15 * 60 + 15) is None
    assert push.check_stress(day, slots + slots_from("15:00", [30, 30, 80, 80]), [], 16 * 60) is None
    assert len(service.sent) == 1
    with app.state.sessions() as db:  # once the cooldown has passed, a new stretch alerts
        db.query(StressAlert).update({StressAlert.sent_at: utcnow() - timedelta(hours=3)})
        db.commit()
    assert push.check_stress(day, slots + slots_from("15:00", [30, 30, 80, 80]), [], 16 * 60)["start"] == 15 * 60 + 30
    assert [a["start"] for a in client.get("/api/push").json()["recent"]] == ["15:30", "14:15"]


def test_stress_alert_uses_garmin_stress_periods_without_15_minute_levels(app):
    push = app.state.push
    events = [{"type": "stress", "start": "13:10", "end": "13:25"}, {"type": "stress", "start": "14:05", "end": "14:50"},
              {"type": "recovery", "start": "15:00", "end": "16:00"}]
    run = push.stress_stretch([{"time": "14:00", "hr": 80}], events, 15 * 60)
    assert run == {"start": 14 * 60 + 5, "end": 14 * 60 + 50, "minutes": 45, "peak": None, "average": None}


def test_day_chart_flags_high_stress():
    slots = [{"time": "08:00", "hr": 60, "hr_min": 55, "hr_max": 65}] + slots_from("10:00", [20, 50, 80, 90, 40])
    summary = {"stress": {"average": 40, "rest_minutes": 300, "low_minutes": 100, "high_minutes": 45},
               "sleep": {"end": "2026-11-26 07:00"}}
    v = health_view.day_chart(date(2026, 11, 26), slots, summary=summary)
    assert [b["band"] for b in v["stress"]["bars"]] == ["rest", "up", "high", "high", "up"]
    assert len(v["high_runs"]) == 1 and "10:30–11:00" in v["high_runs"][0]["label"]
    assert v["stress_peak"]["value"] == 90 and v["stress_peak"]["time"] == "10:45"
    assert v["stress"]["high_minutes"] == 30 and "high for 30m" in v["stress"]["sub"]
    tile = next(t for t in v["tiles"] if t["label"] == "Stress")
    assert tile["sub"] == "Mostly at rest · 45m high" and tile["warn"]
    calm = health_view.day_chart(date(2026, 11, 26), slots_from("10:00", [20, 50, 60]), summary={"sleep": {"end": "2026-11-26 07:00"}})
    assert calm["stress_peak"] is None and calm["high_runs"] == []


def test_encryption_matches_rfc_8291_example():
    server = ec.derive_private_key(int.from_bytes(unb64url("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = encrypt(unb64url("V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"),
                   "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                   "BTBZMqHH6r4Tts7J_aSIgg", salt=unb64url("DGv6ra1nlYgDCS1FRnbzlw"), server_key=server)
    assert b64url(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6"
                            "PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_background_check_reads_todays_timeline_without_garmin(app, client):
    assert app.state.check_stress() is None  # nothing linked or stored: no sync attempt, no alert
