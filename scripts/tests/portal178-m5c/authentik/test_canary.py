#!/usr/bin/env python3
"""Disposable UserInfo proof against the image that is actually mounted.

Run inside the pinned base image or the patched candidate, network disabled.
Classifies the artifact by the on-disk utils hash. Does not apply an in-memory
patch. Prints branch, status, and visibility booleans only.
"""
import contextlib
from datetime import timedelta
import difflib
import hashlib
import importlib
import io
import json
import logging.config
import os
from pathlib import Path
import sys
import traceback

SOURCE_SHA = "131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb"
PATCHED_SHA = "ae1494c6c145d094fd1ad473517f4c0e5fbb8fdfc7b145c651235f29f1871b97"
PROPOSAL_SHA = "ad87572ad62d6ef45ae8711d212534d5c8b5ded76585cc775a4e68ae69afa772"
MARKERS = {
    "opaque": "PORTAL178_SYNTHETIC_OPAQUE_NONCREDENTIAL",
    "jwt-shaped": "eyJhbGciOiJub25lIn0.eyJzdWIiOiJTWU5USEVUSUMifQ.SYNTHETIC",
}
MESSAGES = {
    "missing": ("Token does not exist", "debug"),
    "expired": ("Token has expired", "debug"),
    "revoked": ("Revoked token was used", "warning"),
}
phase = "setup"
results = []
captured = io.StringIO()


def observe(logs, message, marker):
    observed = False
    visible = False
    formatted = False
    severity = False
    expected_level = None
    for level_name, (text, severity_name) in ((name, MESSAGES[name]) for name in MESSAGES):
        if text == message:
            expected_level = severity_name
    for line in logs.splitlines():
        if marker in line:
            visible = True
        if message and message in line:
            observed = True
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            formatted = parsed.get("event") == message
            severity = parsed.get("level") == expected_level
    return observed, visible, formatted, severity


def run():
    global phase
    os.nice(19)
    sys.path.insert(0, "/")
    import django
    django.setup()
    from django.db import connections
    from django.db.models import Model
    from django.test import RequestFactory
    from django.utils.timezone import now
    from unittest.mock import patch
    from authentik.core.models import User
    from authentik.events.models import Event
    from authentik.lib.logging import get_log_level, get_logger_config, structlog_configure
    from authentik.providers.oauth2 import utils
    from authentik.providers.oauth2.models import AccessToken, OAuth2Provider, ScopeMapping
    from authentik.providers.oauth2.views import userinfo
    from types import SimpleNamespace

    for connection in connections.all():
        connection.cursor = lambda *a, **k: (_ for _ in ()).throw(AssertionError("database access"))
    structlog_configure()
    logging.config.dictConfig(get_logger_config())
    level = get_log_level()
    if level not in ("DEBUG", "INFO"):
        raise AssertionError("unsupported log level")
    original = Path(utils.__file__).read_bytes()
    utils_sha = hashlib.sha256(original).hexdigest()
    if utils_sha == SOURCE_SHA:
        artifact = "base"
    elif utils_sha == PATCHED_SHA:
        artifact = "patched"
    else:
        raise AssertionError("utils hash is neither base nor patched")
    proposal = Path("/proposal/authentik-userinfo-log.patch").read_bytes()
    if hashlib.sha256(proposal).hexdigest() != PROPOSAL_SHA:
        raise AssertionError("proposal hash mismatch")
    if artifact == "base":
        text = original.decode()
        corrected = text
        for message, severity_name in MESSAGES.values():
            before = 'LOGGER.%s("%s", access_token=access_token)' % (severity_name, message)
            if corrected.count(before) != 1:
                raise AssertionError("base image is missing a logging call")
            corrected = corrected.replace(before, 'LOGGER.%s("%s")' % (severity_name, message), 1)
        diff = "".join(difflib.unified_diff(
            text.splitlines(keepends=True), corrected.splitlines(keepends=True),
            fromfile="a/authentik/providers/oauth2/utils.py",
            tofile="b/authentik/providers/oauth2/utils.py"))
        if diff != proposal.decode() or hashlib.sha256(corrected.encode()).hexdigest() != PATCHED_SHA:
            raise AssertionError("proposal does not match the base bytes")
        del corrected
    else:
        text = original.decode()
        for message, severity_name in MESSAGES.values():
            if 'LOGGER.%s("%s", access_token=access_token)' % (severity_name, message) in text:
                raise AssertionError("patched image still has the logging argument")
            if text.count('LOGGER.%s("%s")' % (severity_name, message)) != 1:
                raise AssertionError("patched image lost a logging message")
    # The view module is already imported from the on-disk artifact. Do not exec a patch.
    importlib.reload(userinfo)

    user = User(pk=101, username="synthetic-user", email="synthetic@example.invalid")
    provider = OAuth2Provider(pk=102, name="synthetic-provider", _redirect_uris=[])
    factory = RequestFactory()
    real_new = Event.new
    id_token = json.dumps({
        "iss": "https://authentik.example.invalid/application/o/synthetic/",
        "sub": "synthetic-subject",
        "aud": "synthetic-client",
        "exp": 1, "iat": 1, "auth_time": 1,
        "nonce": None, "claims": {},
    })
    for shape, marker in MARKERS.items():
        for branch in ("missing", "expired", "revoked", "success"):
            phase = artifact + ":" + level + ":" + shape + ":" + branch
            if branch == "missing":
                token = None
            else:
                token = AccessToken(
                    token=marker, user=user, provider=provider,
                    expires=now() + timedelta(minutes=-1 if branch == "expired" else 5),
                    revoked=(branch == "revoked"))
                token._scope = "openid"
                token._id_token = id_token
            events = []

            def mocked_new(**kwargs):
                event = real_new(app="authentik.providers.oauth2.utils", **kwargs)
                events.append(event)
                return event

            request = factory.get(
                "/application/o/userinfo/", secure=True,
                HTTP_HOST="authentik.haynesnetwork.com",
                HTTP_AUTHORIZATION="Bearer " + marker,
                HTTP_USER_AGENT="portal-synthetic-canary")
            request.user = user
            captured.seek(0)
            captured.truncate(0)
            empty_scopes = SimpleNamespace(order_by=lambda *a, **k: [])
            with patch.object(AccessToken.objects, "filter", return_value=SimpleNamespace(first=lambda: token)), \
                 patch.object(Event, "new", side_effect=mocked_new) as event_new, \
                 patch.object(Model, "save", autospec=True) as save_mock, \
                 patch.object(ScopeMapping.objects, "filter", return_value=empty_scopes), \
                 patch("authentik.events.models.get_current_tenant", return_value=SimpleNamespace(event_retention="days=1")), \
                 patch("authentik.events.models.DEFAULT_BRAND", SimpleNamespace(
                     name="synthetic-brand", pk=103,
                     _meta=SimpleNamespace(app_label="authentik_brands", model_name="brand"))), \
                 patch("authentik.events.models.get_context_processors", return_value=[]), \
                 patch("authentik.events.models.ClientIPMiddleware.get_client_ip", return_value="127.0.0.1"):
                response = userinfo.UserInfoView.as_view()(request)
            if save_mock.called:
                raise AssertionError("model save was called")
            logs = captured.getvalue()
            body = response.content.decode()
            header_blob = str(dict(response.headers))
            response_visible = marker in body or marker in header_blob
            event_visible = any(marker in json.dumps(event.context, default=str) for event in events)
            if branch == "success":
                payload = json.loads(body)
                success_subject = response.status_code == 200 and payload.get("sub") == "synthetic-subject"
                observed = visible = formatted = severity_ok = False
                if any(text in logs for text, _severity in MESSAGES.values()):
                    observed = True
                visible = marker in logs
                denial_ok = False
                if not success_subject or response_visible or event_visible or event_new.called or visible:
                    raise AssertionError("successful userinfo changed")
            else:
                message, severity_name = MESSAGES[branch]
                observed, visible, formatted, severity_ok = observe(logs, message, marker)
                success_subject = False
                denial_ok = (
                    response.status_code == 401
                    and 'error="invalid_token"' in header_blob
                    and not response_visible)
                event_message = branch == "revoked" and len(events) == 1 and events[0].context.get("message") == "Revoked access token was used"
                if not denial_ok or event_visible or event_new.called is not (branch == "revoked"):
                    raise AssertionError("denial response changed")
                if branch == "revoked" and not event_message:
                    raise AssertionError("revoked event message changed")
                if level == "DEBUG":
                    if not (observed and formatted and severity_ok and severity_name == MESSAGES[branch][1]):
                        raise AssertionError("debug branch was not observed")
                    if visible is not (artifact == "base"):
                        raise AssertionError("debug marker visibility does not match the artifact")
                elif branch == "revoked":
                    if not (observed and formatted and severity_ok):
                        raise AssertionError("revoked warning was not observed at info")
                    if visible is not (artifact == "base"):
                        raise AssertionError("info warning marker visibility does not match the artifact")
                else:
                    if observed or visible:
                        raise AssertionError("info showed a debug denial")
            results.append({
                "shape": shape,
                "branch": branch,
                "status": response.status_code,
                "logMessageObserved": observed,
                "logCanaryVisible": visible,
                "eventCanaryVisible": event_visible,
                "responseCanaryVisible": response_visible,
                "eventPathExercised": bool(event_new.called),
                "formattedJson": formatted,
                "severityObserved": severity_ok,
                "denialUnchanged": denial_ok,
                "successSubject": success_subject,
            })
    return {
        "proof": True,
        "artifact": artifact,
        "utilsSha256": utils_sha,
        "logLevel": level,
        "debugEnabled": level == "DEBUG",
        "cases": results,
        "realTokens": False,
        "databaseWrites": False,
        "productionTokenLeakProven": False,
    }


try:
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        report = run()
except BaseException as error:
    print(json.dumps({
        "phase": phase, "proof": False, "errorType": type(error).__name__, "cases": results,
        "frames": [{"file": Path(frame.filename).name, "line": frame.lineno}
                   for frame in traceback.extract_tb(error.__traceback__)[-12:]],
    }, sort_keys=True))
    raise SystemExit(1)
print(json.dumps(report, sort_keys=True))
